# تحديث استخدام واجهة تيليجرام (المراحل 1–8 + رابط المكتبة في المجموعات)

تاريخ التقرير: 2026-11-12 · وثيقة فقط (لا كود إنتاج، ولا اختبارات، ولا commit/push).
كل ما هو مذكور هنا مُتحقَّق من الكود الموجود في المستودع، وكل التحقق الآلي كان **أوفلاين**
(بلا اتصال بـTelegram أو PostgreSQL أو Render). التفاصيل التاريخية للترقية:
`docs/pytba-upgrade-audit.md`؛ خطة التحديث العامة: `docs/modernization-plan.md`.

## الإطار العام

- المكتبة: `pyTelegramBotAPI==4.37.1` (Bot API 10.2/10.3-era)، مثبّتة في `requirements.txt`.
- المبدأ في كل مرحلة: إضافة صغيرة، مع بديل (fallback) يحفظ السلوك القديم، وفشل صامت
  لا يكسر تدفق البوت، وعدم تسجيل أي نص رسالة أو معرّف سرّي.
- ملفات جديدة تحت `handlers/`: `rich_reports.py`, `ephemeral_messages.py`,
  `maintenance_progress.py`, `maintenance_checklists.py`, `telegram_rate_limit.py`.

## المرحلة 1 — أزرار معطّلة، زر نسخ، Markdown→HTML، blockquote

- `handlers/button_styles.py`: `DisabledInlineKeyboardButton` / `disabled_button()` تستخدم
  `DisabledButton()` الرسمي، وتُزيل `callback_data` في `to_dict()` (تسلسل `"disabled": {}`)
  — أزرار العرض فقط (عناوين أقسام، عدّادات صفحة، إحصاءات للقراءة) لم تعد قابلة للنقر.
- `/myid` في `handlers/user_handlers.py`: `InlineKeyboardButton(..., copy_text=CopyTextButton(text=user_id))`.
- تحويل Markdown→HTML في `miniapp.py`/`handlers/thread_format.py`: كل نص ديناميكي يمرّ عبر
  `html.escape` قبل الإدراج، ووسوم HTML فقط من الكود.
- `/admin/diagnose_file_ids` في `webhook_bot.py`: التفاصيل داخل `<blockquote expandable>`
  واحدة، وكل قيمة (caption/النوع/المعرّف/نص الاستثناء) مُهرَّبة بـ`html_escape` ومقصوصة.

## المرحلة 2 — `allowed_updates` مضبوطة

`WEBHOOK_ALLOWED_UPDATES` في `webhook_bot.py` = `message`, `edited_message`, `channel_post`,
`edited_channel_post`, `callback_query`, `inline_query` **+ `my_chat_member` و
`chosen_inline_result` فقط**. `chat_member` **غير مضاف** قصداً (تعريفَه في الـwebhook
يفتح تدفّق تحديثات لا معالج له).

## المرحلة 3 — تقارير Rich

- `handlers/rich_reports.py` يبني `InputRichMessage` بـ`is_rtl=True` من كتل حقيقية:
  `InputRichBlockSectionHeading`, `InputRichBlockList`/`InputRichBlockListItem`,
  `InputRichBlockParagraph`, `InputRichBlockDetails` (لا HTML خام داخل `InputRichMessage`).
- `send_rich_or_plain()`: محاولة واحدة عبر `bot.send_rich_message`، وأي استثناء أو نتيجة
  `None` ⇒ بديل واحد نصي HTML بـ`send_message(..., parse_mode='HTML')` — بلا تكرار للتقرير.
- المستخدمون: تقرير اكتمال البث (`handlers/admin_broadcast.py` → `build_broadcast_report`)
  وتقرير إحصاءات المحتوى (`handlers/callback_handlers.py` → `build_stats_report`).

## المرحلة 4 — رسائل Ephemeral لإدارة المجموعات

- `handlers/ephemeral_messages.py`: `bot.send_message(..., ephemeral_message_parameters=EphemeralMessageParameters(receiver_user_id=admin_id))`
  فقط في `group`/`supergroup` ولأدمن معروف.
- **لا بديل عام** عند الفشل (لا `reply_to` ولا رد مرئي) لأن ذلك يكشف محتوى الإدارة.
- فحص ملكية الحالة (`read_state_row`): أي حالة تخصّ أدمن آخر أو تعذّرت قراءتها ⇒ رفض
  صامت (fail closed). يُستدعى من `handlers/admin_handlers.py` (لوحة الأدمن خارج الخاص)
  و`handlers/user_handlers.py` (أوامر التعليقات/العنوان في المجموعات).
- **أوامر الأدمن في المجموعات تُرفض** دائماً؛ الإشعار نفسه هو الرد الوحيد.

## المرحلة 5 — تقدم العمليات الطويلة بمسودات

- `handlers/maintenance_progress.py`: `MaintenanceProgress` يحاول
  `bot.send_message_draft(chat_id, draft_id, text)` بمعرّف مسودة غير صفري ثابت لكل عملية،
  والقبول فقط عند النتيجة `True`؛ أي استثناء أو `False`/`None` أو غياب الدالة ⇒ تحوّل دائم
  إلى `bot.edit_message_text(text, chat_id, message_id)` بلا إعادة محاولة للمسودة.
- مستخدم في: `scripts/heal_archive.py`, `scripts/convert_docs_to_video.py`, `update_metadata.py`.
- التقارير النهائية تبقى رسائل/تعديلات عادية، لا مسودات.

## المرحلة 6 — قوائم متابعة اختيارية (Checklists)

- `handlers/maintenance_checklists.py`: `InputChecklist`/`InputChecklistTask` ثم
  `bot.send_checklist(business_connection_id, chat_id, checklist)` **بعد** التقرير النهائي.
- التفعيل فقط عندما `TELEGRAM_BUSINESS_CONNECTION_ID` غير فارغ لحظة النداء؛ **لا معرّف
  افتراضي** في الكود، وعند غيابه تُتخطى الميزة بلا أي استدعاء لتيليجرام.
- تتطلب حساب عمل (business connection) متصلاً بالبوت؛ ليست متاحة لكل بوت.
- العمليات: `heal_archive`, `convert_all_docs`, `update_metadata`. لا يُسجَّل معرّف الربط ولا نص القائمة.

## المرحلة 7 — تكاملات Mini App SDK

في `miniapp.py` (صفحة `/app`)، كل تكامل بكشف وجود الطريقة وحده (بلا فحص إصدار) وتدهور أنيق:

- `requestFullscreen`/`exitFullscreen` + `isFullscreen` بزر بنقرة المستخدم فقط؛ عميل بلا
  `requestFullscreen` ⇐ `expand()` بدل تخويل غير موجود.
- `BackButton.onClick`/`show()` يُسجَّل مرة لكل كائن، والرجوع `close()` ثم `history.back()`.
- `safeAreaInset` و`contentSafeAreaInset` تُقرأ مع `onEvent('safeAreaChanged'/'contentSafeAreaChanged')`
  وتُنشر كمتغيّرات CSS `--tg-safe-*` / `--tg-content-safe-*` (قيمة شاذة ⇒ 0).
- `HapticFeedback.impactOccurred`/`notificationOccurred` للنجاح/الفشل فقط عند التوفّر.
- `addToHomeScreen()` يظهر الزر فقط إذا وُجدت الدالة؛ وإلا يبقى مخفياً/معطّلاً بشرح.
- `shareMessage(text, callback)` ثم `openTelegramLink` (رابط `t.me/share/url`) ثم `navigator.share` ثم النسخ.

## المرحلة 8 — احترام 429 / `retry_after`

- `handlers/telegram_rate_limit.py`: `retry_after_seconds()` يقرأ **فقط**
  `exc.error_code == 429` و`exc.result_json['parameters']['retry_after']` (الشكل الرسمي
  في `ApiTelegramException` 4.x؛ لا `exc.parameters`، ولا استنتاج من نص الوصف).
- المقبول: `int`/`float` موجب ومنتهٍ أو نص رقمي؛ المرفوض: `bool`, صفر/سالب, `NaN`/`inf`,
  التالف والغائب ⇒ `None`. القيمة الرسمية تُستخدم كما هي بلا سقف.
- `sleep_after_429(error, sleeper)` يُنيم مرة واحدة ويعيد `True`، و**لا يعيد تشغيل** العملية الفاشلة.
- مواضع التطبيق: `admin_broadcast.run_broadcast`, `scripts/heal_archive.py`,
  `scripts/convert_docs_to_video.py`, `handlers/maintenance_progress.py` (المسودة والتعديل)،
  ومسارات الأدمن الجماعية في `webhook_bot.py`: `admin_update_thumbnails`,
  `admin_extract_channel_thumbnails`, `admin_fix_videos_professional`,
  `admin_diagnose_file_ids`, `admin_fix_content_types`, `admin_force_refresh_all_file_ids`.
- البدائل القديمة (انتظار ثابت 35 ثانية لنص `flood`، و10 ثوانٍ لنص `Retry in`) تعمل
  **فقط** عندما يعيد المساعد `False` (أي القيمة الرسمية غائبة).

## رابط المكتبة داخل المجموعات

- `handlers/user_handlers.py`: `library_group_link()` يبني
  `https://t.me/<bot>?startapp=library` من اسم بوت مُتحقَّق (`[A-Za-z0-9_]{5,32}`، من
  `MINIAPP_BOT_USERNAME` ثم `get_me`) وبشرط `APP_URL` بـhttps؛ أي خلل ⇒ `None`.
- `build_group_library_markup()` يعيد **زر URL عادي** (`InlineKeyboardButton(url=...)`)، ويُستخدم
  في رسالة الترحيب عند إضافة البوت للجروب (`handlers/group_handlers.py`) وفي `/library` داخل
  `group`/`supergroup` (مع نص عربي يوضح أنه يفتح المكتبة في هذه المحادثة).
- زر `web_app` يبقى **في الخاص فقط** (قيد تيليجرام: `web_app` غير مدعوم في المجموعات، وكذلك
  زر القائمة لكل محادثة) — سلوك `/library` في الخاص لم يتغير.
- التطبيق المفتوح من الرابط المباشر **لا يملك وصولاً إلى رسائل المحادثة**؛ لا `chat_id`
  ولا إرسال جماعي، والمعرّف في `initData` هو `chat_type`/`chat_instance` للسياق فقط.

## مؤجَّل: HTML Widgets (Bot API 10.4)

`sendWidget` / `answerWidgetQuery` وأنواع `Widget*` (`WidgetInfo` …) **غير موجودة** في
`pyTelegramBotAPI 4.37.1` المثبّتة (تحقّق الفحص `scripts/pytba_probe.py`: `has_WidgetInfo: False`،
ولا تعريف لها في الحزمة). لذلك **لم تُنفَّذ ولم تُحاكَ**: لا كود وهمي ولا زر أو مسار يدّعي الدعم.
شرط العودة: إصدار مكتبة يوجد فيه هذا السطح فعلياً (ثم ترقية مثبّتة + محاكاة أوفلاين + مسار
بديل صريح). البديل الحالي للمحتوى المهيكل هو عائلة `InputRichMessage` (المرحلة 3).

## مؤجَّل / غير مغطّى صراحةً

- `stopped_message_generation` / `can_stop`: لا معالج ولا قيمة `can_stop` في البوت.
- مسودات الرسائل الغنية (`send_rich_message_draft`): غير مستخدمة؛ التقدم بالمسودات النصية فقط.
- سطح managed-bot (`set/get_managed_bot_access_settings`) واستعلامات الضيوف
  (`answer_guest_query`, `register_guest_message_handler`): غير مستخدمة.
- أي ميزة Bot API 10.4 وما بعدها لا توجد في المكتبة المثبّتة: غير مدعومة وبلا محاكاة.

## إثبات الاختبار (أوفلاين فقط)

| البوابة | النتيجة |
| --- | --- |
| `python -m unittest discover -s tests -v` (الحزمة التراكمية النهائية) | **666 tests OK** |
| البوابة المركّزة للمرحلة 8 (`tests/test_telegram_phase8.py`) | **60 tests OK** |
| بوابة إطلاق المكتبة في المجموعات (`tests/test_miniapp_group_launch.py`) | **25 tests OK** |
| فحص Mini App في متصفح Chromium محلي (SDK مُبدَّل محلياً) | **73 checks** |

- الاختبارات تفصل المسؤوليات: `tests/test_telegram_phase1.py` … `phase8.py` لكل مرحلة،
  و`tests/test_miniapp_sdk_phase7.py` لتكاملات SDK، و`tests/test_miniapp_group_launch.py` للرابط.
- `tests/support.py` يحجب `telebot` HTTP و`psycopg2.connect` قبل استيراد كود الإنتاج، وكل
  `bot`/قاعدة/ساعة/خيط بديل صريح بالاسم. **لا يوجد أي تحقق حقيقي ضد Telegram أو قاعدة البيانات
  أو Render في أي من هذه البوابات.**

## الحدود المعروفة

- زر `web_app` غير مدعوم في المجموعات، ورابط `startapp` المباشر قد يفتحه بعض العملاء في
  الخاص بدل المجموعة، ولا يمنح التطبيق رسائل المحادثة.
- رسائل Ephemeral تظهر فقط للأدمن المستهدف وفي العملاء الداعمة؛ فشلها بلا بديل عام.
- `send_message_draft` قد لا تدعمه بعض العملاء ⇒ التقدم يُرسل بتعديل رسالة عادية.
- القوائم تطالب بحساب عمل متصل؛ بلا `TELEGRAM_BUSINESS_CONNECTION_ID` لا تُرسل إطلاقاً.
- تقارير Rich قد ترفضها عملاء أقدم ⇒ نص HTML بديل واحد فقط.
- انتظار `retry_after` الرسمي الكبير يُوقف خيط العمل الحالي (ليس قائمة انتظار موزّعة)،
  ولا يُعاد تشغيل أي إرسال فاشل.
- الحارس (rate limit) لكل عملية Python، فليس ضماناً عبر عدة workers.

## قائمة التحقق الحيّة (بعد النشر على Render)

متغيرات البيئة على Render: `BOT_TOKEN`, `DATABASE_URL`, `CHANNEL_ID`, `ADMIN_IDS`,
**`MINIAPP_BOT_USERNAME`** (اسم البوت المستخدم في رابط المجموعة)، **`APP_URL`** (https إلزامي)،
واختياري **`TELEGRAM_BUSINESS_CONNECTION_ID`** (بدونه تُتخطى القوائم). لا تُوضع هذه القيم
في المستودع. المتغيرات المطلوبة في `README.md` → *Optional Environment Variables*.

1. **المرحلة 1**: افتح `/myid` ⇒ زر «نسخ المعرّف» يعمل. لوحة الأدمن ⇒ العناوين معطّلة (غير
   قابلة للنقر بلا ردّ callback). `GET /admin/diagnose_file_ids` ⇒ التقرير داخل اقتباس قابل للطي.
2. **المرحلة 2**: `GET /webhook_info` ⇒ `allowed_updates` تحتوي `my_chat_member` و
   `chosen_inline_result` ولا تحتوي `chat_member`.
3. **المرحلة 3**: أكمل بثاً تجريبياً ⇒ تقرير Rich (العربية RTL)؛ وعملية «إحصائيات المحتوى»
   ⇒ تقرير Rich بأعداد صحيحة. جرّب في عميل قديم للتأكد من ظهور نص HTML البديل.
4. **المرحلة 4**: كأدمن أرسل أمر لوحة في مجموعة ⇒ إشعار ephemeral لك وحدك، ويرفض الأمر.
5. **المرحلة 5**: شغّل صيانة (`heal_archive`/تحويل المستندات/`update_metadata`) من الخاص
   ⇒ تقدم «يتحرك» بمسودة أو تعديل رسالة، ثم تقرير نهائي واحد.
6. **المرحلة 6**: بإضافة `TELEGRAM_BUSINESS_CONNECTION_ID` وحساب عمل متصل ⇒ قائمة متابعة بعد
   التقرير؛ وبدونه ⇒ لا رسالة إضافية إطلاقاً.
7. **المرحلة 7**: افتح `/app` في Telegram ⇒ ملء الشاشة، زر الرجوع، عدم تغطية أسفل الشاشة،
   اهتزاز خفيف، «إضافة للشاشة الرئيسية» عند دعم العميل، ومشاركة المكتبة.
8. **المرحلة 8**: لا تُختبر 429 حقيقياً في الإنتاج؛ راجع سجلات أي `retry_after` (لا تُسجّل
   الحمولة) وأكّد أن الانتظار يتبعه توقف الخيط لا إعادة إرسال.
9. **المجموعة**: أضف البوت إلى مجموعة تجريبية ⇒ رسالة ترحيب بزر «مكتبة الفيديو» URL؛ أرسل
   `/library` في المجموعة ⇒ زر URL يفتح المكتبة في المحادثة (`chat_type=group` في `initData`).
   افتح `/library` في الخاص ⇒ زر `web_app` كالمعتاد.

ملاحظة صادقة: كل ما سبق آلياً كان **أوفلاين**؛ هذه القائمة هي مسار التحقق الوحيد من طرف إلى طرف.
