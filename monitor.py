#!/usr/bin/env python3
"""Public-page monitor for sport1live24.blogspot.com."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

SITE = "https://sport1live24.blogspot.com"
STATE_PATH = Path("data/state.json")
USER_AGENT = "SportLive24Monitor/1.0 (+public-page monitoring)"
TIMEOUT = 20
MAX_PAGES = 35
MAX_POSTS_PER_RUN = 250
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalized(url: str) -> str:
    url = urldefrag(url.strip())[0]
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    host = (p.hostname or "").lower()
    if host.endswith("blogspot.com") and host != "sport1live24.blogspot.com":
        return ""
    if host not in ("sport1live24.blogspot.com",):
        return ""
    # Blogger post identity is stable at the canonical URL; discard tracking params.
    return f"https://{host}{p.path.rstrip('/') or '/'}"


def fetch(url: str):
    try:
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        print(f"WARN fetch failed: {url} :: {exc}", file=sys.stderr)
        return None


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"State file is unreadable; refusing to overwrite it: {exc}")
    return {"schema_version": 1, "site": SITE, "last_run_utc": None, "posts": {}, "run_history": []}


def parse_feed(url: str) -> list[dict]:
    response = fetch(url)
    if not response:
        return []
    entries = []
    content_type = response.headers.get("Content-Type", "")
    body = response.text
    # Blogger JSON feed
    if "json" in content_type or body.lstrip().startswith("{"):
        try:
            data = response.json()
            for item in data.get("feed", {}).get("entry", []):
                links = item.get("link", [])
                href = next((x.get("href") for x in links if x.get("rel") == "alternate"), None)
                title = item.get("title", {}).get("$t", "")
                published = item.get("published", {}).get("$t") or item.get("updated", {}).get("$t")
                if href:
                    entries.append({"url": href, "title": title, "published": published})
        except Exception:
            pass
        return entries
    # Atom / RSS
    try:
        root = ET.fromstring(body)
        for node in root.iter():
            tag = node.tag.split("}")[-1].lower()
            if tag not in ("entry", "item"):
                continue
            title = ""
            href = ""
            published = ""
            for child in list(node):
                name = child.tag.split("}")[-1].lower()
                if name == "title":
                    title = "".join(child.itertext()).strip()
                elif name in ("published", "updated", "pubdate"):
                    published = "".join(child.itertext()).strip()
                elif name == "link":
                    rel = child.attrib.get("rel", "alternate")
                    candidate = child.attrib.get("href") or "".join(child.itertext()).strip()
                    if candidate and rel in ("alternate", ""):
                        href = candidate
            if href:
                entries.append({"url": href, "title": title, "published": published})
    except ET.ParseError:
        pass
    return entries


def collect_links(page_url: str, html: str) -> tuple[set[str], set[str]]:
    soup = BeautifulSoup(html, "html.parser")
    posts, pages = set(), set()
    for a in soup.find_all("a", href=True):
        absolute = urljoin(page_url, a["href"])
        clean = normalized(absolute)
        if not clean:
            continue
        path = urlparse(clean).path.lower()
        if re.search(r"/\d{4}/\d{2}/[^/]+\.html$", path):
            posts.add(clean)
        elif path == "/" or any(x in path for x in ("/search/label/", "/search", "/20")):
            pages.add(clean)
    return posts, pages


def discover() -> tuple[dict[str, dict], set[str], list[str]]:
    found: dict[str, dict] = {}
    archive_pages: set[str] = set()
    errors: list[str] = []

    feed_urls = [
        f"{SITE}/feeds/posts/default?alt=json&max-results=500",
        f"{SITE}/feeds/posts/default?alt=atom&max-results=500",
        f"{SITE}/feeds/posts/default?alt=rss&max-results=500",
    ]
    for feed_url in feed_urls:
        for entry in parse_feed(feed_url):
            link = normalized(entry.get("url", ""))
            if link:
                found[link] = {
                    "title": entry.get("title", "").strip(),
                    "published": entry.get("published"),
                }

    for sitemap in (f"{SITE}/sitemap.xml", f"{SITE}/sitemap-pages.xml"):
        response = fetch(sitemap)
        if not response:
            continue
        try:
            root = ET.fromstring(response.text)
            for loc in root.iter():
                if loc.tag.split("}")[-1].lower() == "loc" and loc.text:
                    link = normalized(loc.text)
                    if link and re.search(r"/\d{4}/\d{2}/[^/]+\.html$", urlparse(link).path):
                        found.setdefault(link, {"title": "", "published": None})
        except ET.ParseError:
            errors.append(f"تعذر تحليل خريطة الموقع: {sitemap}")

    queue = [SITE + "/", SITE + "/search?max-results=100"]
    queue.extend(f"{SITE}/search?updated-max=2099-12-31T23:59:59-00:00&max-results=100&start={i}"
                 for i in range(0, 500, 100))
    visited = set()
    while queue and len(visited) < MAX_PAGES and len(found) < MAX_POSTS_PER_RUN:
        page = queue.pop(0)
        if page in visited:
            continue
        visited.add(page)
        response = fetch(page)
        if not response:
            continue
        posts, pages = collect_links(response.url, response.text)
        for link in posts:
            found.setdefault(link, {"title": "", "published": None})
        for candidate in pages:
            if candidate not in visited and candidate not in queue:
                # Follow Blogger archive, label, and pagination pages only.
                path = urlparse(candidate).path.lower()
                if path == "/" or "/search" in path:
                    queue.append(candidate)
    return found, archive_pages, errors


def inspect_post(url: str) -> dict | None:
    response = fetch(url)
    if not response:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    canonical_url = normalized(urljoin(response.url, canonical.get("href"))) if canonical else normalized(response.url)
    if not canonical_url:
        canonical_url = normalized(url)
    title_tag = soup.find("h1", class_=re.compile("post-title", re.I)) or soup.find("meta", property="og:title") or soup.title
    title = title_tag.get("content", "") if title_tag and title_tag.name == "meta" else (title_tag.get_text(" ", strip=True) if title_tag else "")
    article = soup.find("div", class_=re.compile(r"(post-body|post-content|entry-content)", re.I))
    if not article:
        article = soup.find("article") or soup.body or soup
    text = re.sub(r"\s+", " ", article.get_text(" ", strip=True)).strip()
    published = None
    date_tag = soup.find("meta", property="article:published_time") or soup.find("time", datetime=True)
    if date_tag:
        published = date_tag.get("content") or date_tag.get("datetime")
    links = []
    for a in article.find_all("a", href=True):
        href = urljoin(response.url, a["href"])
        if urlparse(href).scheme in ("http", "https"):
            links.append(href)
    images = []
    for img in article.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            images.append(urljoin(response.url, src))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "url": canonical_url,
        "title": title.strip(),
        "published": published,
        "content_hash": digest,
        "text_length": len(text),
        "links": sorted(set(links))[:100],
        "images": sorted(set(images))[:100],
        "read_at_utc": utc_now(),
        "read_status": "read" if len(text) >= 80 else "possibly_incomplete",
    }


def write_summary(summary: str):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(summary + "\n")


def main():
    started = utc_now()
    state = load_state()
    old_posts = state.setdefault("posts", {})
    discovered, _, errors = discover()
    if not discovered and not old_posts:
        raise RuntimeError("لم يتمكن البرنامج من اكتشاف أي منشور أو مصدر؛ لن يسجل ذلك كعدم وجود أخبار.")

    new_posts, changed_posts = [], []
    checked, incomplete = 0, 0
    # Inspect all discovered posts, prioritizing unseen posts then recent-looking URLs.
    urls = sorted(discovered, key=lambda u: (u in old_posts, u), reverse=False)[:MAX_POSTS_PER_RUN]
    for url in urls:
        metadata = inspect_post(url)
        if not metadata:
            continue
        checked += 1
        if metadata["read_status"] == "possibly_incomplete":
            incomplete += 1
        key = metadata["url"]
        previous = old_posts.get(key)
        if not previous:
            metadata["first_seen_utc"] = utc_now()
            metadata["change_history"] = []
            old_posts[key] = metadata
            new_posts.append(metadata)
        else:
            if previous.get("content_hash") != metadata.get("content_hash"):
                previous.setdefault("change_history", []).append({
                    "detected_at_utc": utc_now(),
                    "old_hash": previous.get("content_hash"),
                    "new_hash": metadata.get("content_hash"),
                    "new_text_length": metadata.get("text_length"),
                })
                changed_posts.append(metadata)
            # Refresh latest reading metadata without erasing first-seen history.
            for field in ("title", "published", "content_hash", "text_length", "links", "images", "read_at_utc", "read_status"):
                previous[field] = metadata[field]

        time.sleep(0.25)

    state["last_run_utc"] = utc_now()
    state["run_history"].append({
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "discovered_count": len(discovered),
        "checked_count": checked,
        "new_count": len(new_posts),
        "changed_old_count": len(changed_posts),
        "incomplete_count": incomplete,
        "errors": errors[:20],
    })
    state["run_history"] = state["run_history"][-200:]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["## تقرير مراقبة Sport1Live24", "", f"- وقت التشغيل (UTC): {state['last_run_utc']}",
             f"- المنشورات المكتشفة: {len(discovered)}",
             f"- المنشورات التي تمت قراءتها: {checked}",
             f"- المنشورات غير المكتملة/قصيرة المحتوى: {incomplete}",
             f"- تحديثات منشورات قديمة: {len(changed_posts)}",
             f"- أخطاء المصادر: {len(errors)}", ""]
    if new_posts:
        lines += ["### أخبار جديدة"]
        for post in new_posts:
            lines += [f"عنوان الخبر: {post['title'] or 'عنوان غير متاح'}",
                      f"رابط الخبر: {post['url']}", ""]
    else:
        lines += ["### لا توجد أخبار جديدة مؤكدة في هذا التشغيل."]
    if changed_posts:
        lines += ["### تحديثات في منشورات سابقة"]
        for post in changed_posts:
            lines += [f"- {post['title'] or post['url']}: {post['url']}"]
    if errors:
        lines += ["### ملاحظات", *[f"- {e}" for e in errors]]
    if checked == 0:
        lines += ["", "**تنبيه:** لم تكتمل قراءة أي منشور؛ لا يمكن اعتبار هذه النتيجة دليلاً على عدم وجود أخبار."]
    report = "\n".join(lines)
    print(report)
    write_summary(report)


if __name__ == "__main__":
    main()
