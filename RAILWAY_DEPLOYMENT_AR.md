# تشغيل واجهة الويب على Railway

هذا دليل نسخة الويب. إعدادات Telegram في `docs/telegram_release/` تاريخية ولا تُستخدم هنا.

1. ارفع محتويات هذا المجلد إلى جذر مستودع GitHub، بحيث يظهر Dockerfile في الجذر. اربطه بخدمة Railway.
2. أضف Volume بمسار `/data`، واترك عدد النسخ **1**. SQLite وقفل العملية مصممان لنسخة واحدة.
3. في Variables أضف `DATA_DIR=/data` و`WEB_ACCESS_PASSWORD` برمز دخول تختاره من 12 حرفًا على الأقل. لا تضع قيمته في المستودع. رمز الدخول مطلوب عند النشر العام.
4. من Networking اختر **Generate Domain**. يستخدم التطبيق `RAILWAY_PUBLIC_DOMAIN` لتحديد عنوان الموقع، ويمكن ضبط `PUBLIC_URL=https://your-domain` يدويًا عند استخدام نطاق مخصص. يستخدم المنفذ الذي توفره Railway في `PORT`.
5. انشر الخدمة وافتح رابطها، ثم أدخل رمز الدخول. افتح إعداد الذكاء الاصطناعي واربط نموذجك، أو اضبط متغيرات النموذج أدناه على الخادم.

التشغيل عبر Docker ENTRYPOINT المحدد في المشروع؛ اترك Start Command دون تجاوز. `/health` يعيد 200 عند جاهزية خادم الويب، وrailway.toml يحدد هذا المسار للفحص. فحص الصحة لا يختبر رصيد أو اتصال مزود النموذج.

| المتغير | الاستخدام |
|---|---|
| `WEB_ACCESS_PASSWORD` | مطلوب للنشر العام؛ 12 حرفًا على الأقل |
| `DATA_DIR` | `/data` مطابق لمسار Volume |
| `DATA_RETENTION_HOURS` | `24` افتراضيًا |
| `PUBLIC_URL` | عنوان HTTPS الأصلي؛ اختياري عند وجود RAILWAY_PUBLIC_DOMAIN |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` افتراضيًا |
| `LLM_MODEL` | معرف نموذج متاح بحساب مزودك، اختياري لتشغيل الوضع المحلي |
| `LLM_API_KEY` | مفتاح الخادم، أو أدخِل مفتاح جلسة من الواجهة |
| `LLM_JSON_MODE` | `auto` |
| `LLM_TIMEOUT_SECONDS` | `35` |
| `LLM_MAX_ATTEMPTS` | `3` |

`.env.example` توثيق فقط؛ لا يحمّل التطبيق ملف .env تلقائيًا. على Railway استخدم Variables. لا تحتاج PostgreSQL أو GPU أو توكن Telegram لهذه النسخة.

بعد أول نشر، تحقق من تسجيل الدخول وإكمال مثال اصطناعي وتنزيل التقرير، ثم Restart للخدمة وتأكد أن المحادثة باقية. مفتاح جلسة الواجهة يُزال من الذاكرة عند Restart؛ متغيرات LLM الخاصة بالخادم تبقى في إعدادات Railway.

لم يُنفذ نشر Railway أو بناء Docker في بيئة العمل المحلية. مهمة GitHub Actions تُثبت اعتماديات التطوير وتشغّل الاختبارات والبناء عند توفر صلاحية تشغيلها؛ راقب نتيجتها داخل المستودع.

مراجع الإعدادات: [Railway Healthchecks](https://docs.railway.com/deployments/healthchecks)، [Railway Volumes](https://docs.railway.com/volumes/reference)، [FastAPI](https://fastapi.tiangolo.com/tutorial/first-steps/).
