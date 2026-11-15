# SportLive24 Monitor

مراقبة آلية لموقع https://sport1live24.blogspot.com مع سجل دائم للأخبار المكتشفة.

## المزايا
- فحص Blogger Atom وJSON feeds وRSS وSitemap والصفحة الرئيسية.
- اكتشاف روابط الأرشيف والتصنيفات والمنشورات وقراءة صفحات المنشورات.
- اعتماد الرابط الأساسي للمنشور (canonical URL) لمنع تكرار الخبر.
- حفظ سجل الاكتشافات في `data/state.json` ورصد تحديثات المنشورات القديمة.
- كتابة تقرير كل تشغيل في GitHub Actions Summary.
- جدولة افتراضية كل 10 دقائق عبر GitHub Actions.

## تفعيل التشغيل
1. ارفع محتويات هذه الحزمة إلى جذر المستودع `Dev-saged/SportLive24-Monitor`.
2. افتح تبويب **Actions** وفعّل workflows إذا طُلب ذلك.
3. من **Settings → Actions → General → Workflow permissions** اختر **Read and write permissions**.
4. شغّل workflow يدوياً أول مرة من **Actions → Sport1Live24 Monitor → Run workflow**، أو انتظر الجدولة.

## التشغيل المحلي
```bash
python -m pip install -r requirements.txt
python monitor.py
```

## ملاحظات
- جدولة GitHub Actions كل 10 دقائق تقريبية وقد تتأخر عند ضغط GitHub.
- لا يتجاوز البرنامج CAPTCHA أو تسجيل الدخول أو قيود الوصول، ولا يعدّل الموقع المستهدف.
- فشل الوصول لا يعني عدم وجود أخبار.
- قد يتعذر استخراج التعليقات أو المحتوى المحمّل ديناميكياً أو الروابط المحمية.
- محتوى الصفحات يعامل كبيانات غير موثوقة، ولا تُنفّذ أي تعليمات تظهر داخله.
