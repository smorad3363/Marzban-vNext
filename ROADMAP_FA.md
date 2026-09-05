# نقشه‌راه اجرایی نسخه بعدی Marzban — فارسی

## 0) قرارداد شروع — فقط از سورس تمیز

این فایل و فایل انگلیسی `ROADMAP_CODEX_EN.md` تنها دستورهای پروژه هستند.

**هیچ پوشه، Clone، STATE، ROADMAP، AGENTS، Graphify output، دستور ترمینال، Patch یا فایل توسعه قبلی را به‌عنوان مبنا استفاده نکن.**

منابع رسمی و تنها baseline:

- ZIP دقیق نسخه: `https://github.com/smorad3363/Marzban/archive/refs/tags/v5.1.0.zip`
- Releases: `https://github.com/smorad3363/Marzban/releases`
- Repository: `https://github.com/smorad3363/Marzban`
- Tag: `v5.1.0`
- Commit مورد انتظار Tag: `c824e822a2f5e41d91b894aabd2a7b9c77a200d2`

### Bootstrap اجباری

1. یک Workspace تمیز بساز.
2. ZIP بالا را **دوباره دانلود کن**؛ از ZIP یا Source قدیمی محلی استفاده نکن.
3. Tag/Commit را از GitHub بررسی کن و با مقدار مورد انتظار مقایسه کن.
4. ZIP را در پوشه جدید `Marzban-vNext` Extract کن.
5. قبل از تغییر کد، فایل `AGENTS.md` موجود در Release را به:
   `docs/legacy/AGENTS.upstream-v5.1.0.md`
   منتقل کن؛ چون شامل دستورهای baseline قدیمی است.
6. هر فایل state/runbook/graphify تولیدشده قدیمی را مبنای تصمیم قرار نده؛ در صورت وجود archive یا regenerate کن.
7. یک `AGENTS.md` کوتاه و جدید از روی همین Roadmap بساز.
8. Git جدید را از همین Source تمیز راه‌اندازی کن:
   - `git init`
   - branch پایه: `vnext-core`
   - remote `upstream` = `https://github.com/smorad3363/Marzban.git`
   - commit اول = `baseline: upstream v5.1.0`
   - tag = `baseline-v5.1.0`
9. هیچ کد قبلی از Workspaceهای دیگر وارد نکن.

---

# 1) ابزارها و Skillهای لازم — حداقل و بدون هم‌پوشانی

فقط این ابزارها لازم‌اند:

### ضروری
- Git
- GitHub CLI (`gh`)
- Docker + Docker Compose
- Python 3.12
- Node.js/npm
- `rg` / ripgrep

### برای کاهش Token و حفظ معماری
**Graphify**
- فقط یک‌بار در شروع، نقشه معماری پروژه را بساز.
- بعد از آن فقط Queryهای هدفمند روی بخش مربوط به Phase جاری انجام بده.
- اگر نصب Graphify به هر دلیل نامطمئن یا خراب بود، پروژه را Block نکن؛ از `rg`, `git grep`, import/call-site search استفاده کن.
- کل Repo را در هر Phase دوباره نخوان.

### مستندات به‌روز
**Context7**
- فقط برای API/Libraryهایی که نسخه‌شان مهم است استفاده شود.
- اگر Context7 در محیط موجود نبود، فقط مستندات رسمی همان Library را بخوان.
- تصمیم‌های نسخه‌ای را یک بار در `docs/CODEX/DECISIONS.md` ثبت کن تا دوباره تحقیق نشوند.

### طراحی UI/UX
**UI/UX Pro Max**
- تنها Skill طراحی مجاز.
- Skill مشابه دوم نصب نکن.
- فقط در Phase UI استفاده شود.

### تست نهایی UI
**Playwright CLI**
- فقط نزدیک تست نهایی نصب/فعال شود.
- Browser MCP دائمی یا ابزار Browser مشابه موازی نصب نکن.

### نصب نکن مگر نیاز واقعی اثبات شود
- Serena
- Taskmaster
- Beads
- Storybook
- Tailwind
- shadcn
- GSAP
- Chart library جدید
- Design Skill دوم

Frontend موجود را حفظ کن:
- React 18
- Chakra UI
- Framer Motion
- ApexCharts

---

# 2) Resume واقعی بعد از قطع Session

حافظه Agent منبع حقیقت نیست.

این فایل‌ها را بساز:

- `docs/CODEX/STATE.md`
- `docs/CODEX/DECISIONS.md`
- `docs/CODEX/ARCHITECTURE.md`
- `docs/CODEX/CHANGELOG_WORK.md`

`STATE.md` باید همیشه کوتاه باشد و فقط شامل این موارد باشد:

- Phase فعلی
- وضعیت Phase
- Git HEAD
- آخرین checkpoint
- فایل‌های در حال تغییر
- کار انجام‌شده
- قدم دقیق بعدی
- blocker واقعی، اگر وجود دارد

### وقتی Session قطع شد و کاربر فقط گفت «ادامه بده»

فقط این ترتیب را بخوان:

1. `AGENTS.md`
2. `docs/CODEX/STATE.md`
3. `git status`
4. آخرین commit
5. diffهای ناتمام
6. Query هدفمند Graphify یا `rg` برای قدم بعدی

**کل پروژه، کل تاریخچه و Roadmap را دوباره تحلیل نکن.**

---

# 3) سیاست Git و قابلیت Rollback UI

این پروژه دو checkpoint اصلی دارد:

## Core checkpoint
تمام تغییرات فنی قبل UI روی branch:

`vnext-core`

بعد از تمام شدن Core:

- Commit کامل بزن.
- Tag بزن: `checkpoint-core-complete`
- وضعیت را در `STATE.md` ثبت کن.
- **متوقف نشو.**
- خودکار branch جدید بساز:

`vnext-ui`

## UI checkpoint
تمام White-Label و UI/UX فقط روی:

`vnext-ui`

انجام شود.

این جداسازی اجباری است تا اگر UI مورد پسند نبود، فقط UI قابل حذف باشد و تمام فیکس‌های فنی Core باقی بمانند.

هیچ تغییر UI را روی `vnext-core` commit نکن.

---

# 4) قانون اجرای خودکار

بعد از دستور «شروع کن»:

- تا پایان پروژه بدون سؤال اضافه جلو برو.
- قبل UI توقف نکن.
- بعد Core فقط checkpoint بزن و خودکار UI را شروع کن.
- هیچ refactor بی‌ربط انجام نده.
- هیچ bug-hunting loop باز انجام نده.
- تست کامل را وسط کار تکرار نکن.
- هیچ فایل یا قابلیت سالمی را بدون نیاز بازنویسی نکن.
- تغییر کوچک را با کوچک‌ترین diff منطقی انجام بده.
- اگر یک تصمیم در این Roadmap مشخص است دوباره درباره‌اش تحقیق یا سؤال نکن.
- اگر مانع خارجی مثل نبود GitHub authentication وجود داشت، توسعه را ادامه بده و فقط مانع Push را در STATE ثبت کن.

---

# PHASE 1 — Installer / CLI / Version Integrity

مشکل اختلاف CLI، Docker image و App version را کامل برطرف کن.

الزامات:

- `latest` برای Release استفاده نشود.
- نصب `v5.1.0` باید دقیقاً Image همان Release را اجرا کند.
- Install/Update باید Pull + Recreate صحیح انجام دهد.
- CLI نصب‌شده دقیقاً متعلق به همان Release باشد.
- README و installer نباید اسکریپت master را برای یک Tag نصب کنند.
- `set-owner` و `mysql-upgrade` همراه همان Release باشند.
- دستور جدید:
  `marzban version`

باید حداقل نشان دهد:

- CLI version
- Runtime app version
- Configured Docker image/tag
- Running Docker image/tag
- Image digest

اگر نسخه‌ها mismatch هستند، Install/Update موفق اعلام نشود.

---

# PHASE 2 — MySQL جدید، ثابت و Migration امن

Fresh installهای نسخه جدید باید با MySQL جدید و Pin‌شده اجرا شوند.

Default هدف:

`mysql:26.7.0`

از `mysql:latest` استفاده نشود.

الزامات:

- Preflight قبل از Start
- تشخیص نسخه دیتابیس موجود
- جلوگیری از downgrade مستقیم data directory
- Backup قبل migration
- برای تغییرات ناسازگار از logical dump/restore استفاده شود
- Fresh install مسیر تمیز داشته باشد
- Existing install مسیر migration مشخص داشته باشد
- Migration بعد از قطع Session قابل Resume باشد
- هیچ دیتابیس موجود خودکار overwrite نشود
- خطای ناسازگاری برای کاربر واضح باشد

---

# PHASE 3 — Dependency Compatibility

فقط Dependency مشکل‌دار را اصلاح کن.

- APScheduler قدیمی که `pkg_resources` مصرف می‌کند آپدیت شود.
- وابستگی deprecated حذف شود.
- workaround مربوط به `setuptools<81` فقط وقتی دیگر لازم نیست حذف شود.
- Broad dependency upgrade انجام نده.

---

# PHASE 4 — جداسازی Plan از Access

## Plan
فقط Commercial entitlement:

- Traffic
- Duration
- Price
- محدودیت‌های تجاری لازم

## Access Group
بخش شبکه:

- Nodes
- Inbounds
- Hosts
- Network access/routing settings

ساختار:

`User → Access Group → Nodes / Inbounds / Hosts`

و User جداگانه رابطه Plan خود را دارد.

### Sync

تغییر در:

- Host
- Inbound
- Node membership

روی Userهای فعال همان Access Group اعمال شود.

ولی تغییر:

- Price
- Traffic
- Duration

در Plan نباید entitlement کاربران قبلی را خودکار تغییر دهد.

Plan حذف واقعی نشود؛ Archive شود تا History و Billing خراب نشود.

---

# PHASE 5 — Admin Policy + Billing + User Creation

یک Policy Engine مرکزی داشته باش که در همه مسیرها استفاده شود:

- Create
- Edit
- Renew
- Quick Renew
- Bulk
- Reset Usage
- API

UI فقط Policy را نمایش دهد؛ امنیت واقعی Backend باشد.

## USED_TRAFFIC

Creation mode:

- Plan Only
- Form Only
- Both

Default:

`Plan Only`

Accounting بر اساس مصرف واقعی کاربران.

Reset Usage نباید مصرفی که قبلاً برای Admin حساب شده را حذف کند.

## ALLOCATED_TRAFFIC

Creation mode:

- Plan Only
- Form Only
- Both

Default:

`Plan Only`

Billing اصلی بر اساس کیف پول تومان باشد.

### Plan
قیمت از خود Plan.

### Form
قیمت:

`GB × PricePerGB × DurationMultiplier`

مثال:

30GB × 1,000 × 1.1 = `33,000 تومان`

Admin حق کاهش حجم ندارد.

Unlimited traffic از Form آزاد ساخته نشود.

## USER_CREDIT

همیشه:

`Plan Only`

Form اصلاً نمایش داده نشود.

Reset Usage روی Credit تعداد User اثر نداشته باشد.

---

# PHASE 6 — Duration / Pricing Settings

Duration دستی برای Admin ممنوع.

Default presetها:

- 1 روز → `0.8`
- 7 روز → `0.9`
- 30 روز → `1.0`
- 60 روز → `1.1`

این Presetها و multiplierها فقط در Owner Settings مدیریت شوند؛ فرم ساخت Admin شلوغ نشود.

Unlimited duration فقط با Permission صریح Owner.

نمایش پول همه‌جا خوانا:

`200,000 تومان`

نه:

`200000`

---

# PHASE 7 — Plan Permission و Quick Renew

## Owner
Plan را:

- ببیند
- بسازد
- ویرایش کند
- Archive کند

## Admin
صفحه و Menu مدیریت Plan را **اصلاً نبیند**.

Admin فقط در دو نقطه Plan را به‌شکل خلاصه ببیند:

1. Create User
2. Quick Renew کنار User

Summary مثال:

`30GB • 30 روز • 33,000 تومان`

Quick Renew:

- Plan انتخاب شود
- خلاصه تغییر نشان داده شود
- Billing/Policy بررسی شود
- Apply شود

---

# PHASE 8 — Bulk User Operations

بخش عملیات گروهی را بازطراحی عملکردی کن.

- همان Userهای تیک‌خورده Target واقعی باشند.
- نیاز به انتخاب دوباره Admin/All Users حذف شود.
- اکشن‌های سازگار قابل ترکیب باشند.
- اکشن‌های ناسازگار همزمان انتخاب نشوند.
- قبل اجرا Preview نشان بده:
  - تعداد User
  - تغییر حجم
  - تغییر مدت
  - تغییر status
  - هزینه
- بعد اجرا برای هر User:
  - Success
  - Failed
  - Reason

همان Policy Engine Phaseهای قبل اعمال شود.

Admin نتواند از Bulk محدودیت Create/Edit/Renew را دور بزند.

---

# PHASE 9 — Device / IP Activity Tracking

سیستم فعلی نباید Device را فقط از log count کوتاه‌مدت حدس بزند.

V1:

- activity بر اساس `last_seen`
- ثبت User + IP + Node + timestamp
- Node Collector heartbeat
- Auto-Reconnect واقعی
- حذف/جایگزینی collector stale
- تشخیص فرق Node telemetry loss با Offline user
- Last log/telemetry timestamp برای هر Node
- بعد restart Master وضعیت به‌شکل بی‌دلیل ناپدید نشود
- هشدار Device limit بر اساس activity همزمان پایدارتر شود

IP را Device ID قطعی فرض نکن.

Data model را برای V2 آماده کن:

**Device Token / Device Slot**

اما V2 را فقط در صورت نیاز واقعی Client اجرا کن.

---

# PHASE 10 — Backup & Restore یکپارچه

Backup داخلی Marzban را با قابلیت‌های مفید Marzban-Backup ترکیب کن.

قسمت‌های مربوط به:

- Sanaei
- 3x-ui
- Hiddify
- سیستم‌های غیر Marzban

حذف شوند.

## Backup باید شامل باشد

- MySQL logical dump
- فایل‌های ضروری Panel
- Config
- Certificates و data لازم
- Manifest
- App/DB version metadata
- Timestamp
- Checksum

Raw `/var/lib/mysql` را به‌عنوان backup format اصلی DB استفاده نکن.

## مقصدها

- Local
- Telegram
- Email
- Telegram + Email

### Telegram
اگر محدودیت حجم وجود داشت فایل split شود.

### Email
Backup کامل به‌صورت یک فایل ارسال شود؛ split نشود.
اگر SMTP limit مانع بود، خطای واضح بده و فایل محلی را سالم نگه دار.

## Schedule

Default preset:

- 15m
- 30m
- 1h
- 3h
- 6h
- 12h
- 24h

Retention قابل تنظیم.

## Backup Settings

فقط Owner:

- Telegram bot/chat
- SMTP
- From/To
- Schedule
- Retention
- Destinations

## Restore از پنل

فقط Owner.

Flow:

`Upload → Validate → Checksum → Pre-Restore Backup → Maintenance → Restore DB + Files → Migrations → Start → Health Check`

Backup ناسالم نباید Restore را شروع کند.

---

# CORE CHECKPOINT — بدون توقف

بعد از Phase 10:

1. `STATE.md` را Update کن.
2. Diff را بررسی کن.
3. Commit Core بزن.
4. Tag:
   `checkpoint-core-complete`
5. Branch `vnext-core` را دست‌نخورده نگه دار.
6. Branch جدید:
   `vnext-ui`
7. **بدون درخواست کاربر فوراً Phase UI را شروع کن.**

---

# PHASE 11 — White-Label کامل

هیچ نام قابل مشاهده‌ای از موارد زیر در UI باقی نماند:

- Marzban
- Heisenberg / هایزنبرگ
- Fork branding قبلی
- Developer branding قبلی
- GitHub/Credits قدیمی

شامل:

- Login
- Dashboard
- Page title
- Meta
- Footer
- Logo
- Favicon
- Manifest
- Loading
- Error pages
- Empty states
- About/version UI

قوانین License و copyright لازم در Source حفظ شوند؛ فقط branding ظاهری حذف شود.

Owner بتواند از Settings تنظیم کند:

- Panel Name
- Logo
- Favicon
- Login Title
- Optional Description

---

# PHASE 12 — UI/UX Redesign حرفه‌ای

هدف:

**Modern Premium Operations Dashboard**

نه UI شلوغ، نه Neon بی‌دلیل، نه Template عمومی AI.

از Stack موجود استفاده کن:

- React
- Chakra UI
- Framer Motion
- ApexCharts

Framework دوم اضافه نکن.

## Design System

قبل از بازطراحی صفحات:

- color tokens
- spacing
- typography
- border radius
- shadow/elevation
- motion timings
- component states
- dark/light behavior
- RTL/LTR rules

را یکپارچه کن.

## Dashboard

اولویت:

- Online Users
- Active Users
- Traffic
- Wallet/Credit
- Node health
- Node online/offline
- Device warnings
- Backup status
- Important events

Chart فقط جایی که اطلاعات واقعی می‌دهد.

## Settings

دسته‌بندی:

- General
- Users
- Admin Policies
- Plans & Pricing
- Access Groups
- Nodes
- Backup & Restore
- Branding
- System

گزینه‌های کم‌استفاده داخل Advanced/Collapsible.

## Users

- Table حرفه‌ای
- Search/Filter واضح
- Bulk actions مرتب
- Quick Renew
- Status واضح
- Device/IP summary
- Action menu یکپارچه

## Admins

فرم کوتاه و قابل فهم.

Duration presets و multiplierها داخل Admin form نمایش داده نشوند.

## Plans برای Admin

هیچ Plan management page/menu.

فقط Plan cards/summary داخل:

- Create User
- Quick Renew

## Motion

Framer Motion برای:

- Page transition
- Drawer/Modal
- Card/KPI entrance
- Hover/focus feedback
- Chart entrance
- Success/error feedback

Motion سبک، سریع و هدفمند باشد.

`prefers-reduced-motion` رعایت شود.

## Responsive

Desktop / Tablet / Mobile کامل.

RTL و LTR هر دو درست.

---

# PHASE 13 — فقط یک Verification نهایی و هدفمند

تا قبل از این Phase Full Test Suite تکرار نکن.

یک Verification نهایی یکپارچه اجرا کن که فقط Critical pathها را پوشش دهد:

1. Fresh install
2. Version integrity
3. MySQL preflight/migration
4. Owner / set-owner
5. Admin policy
6. Create/Edit/Renew/Quick Renew/Reset
7. Plan + Access Group propagation
8. Bulk operations
9. Device/IP collector reconnect
10. Backup
11. Restore round-trip
12. Owner/Admin UI visibility
13. White-label
14. Responsive RTL/LTR
15. Restart + health status

برای Browser از Playwright CLI استفاده کن.

Bug hunting loop ممنوع.

اگر چیزی Fail شد:

`Root cause → Fix → فقط همان مسیر و وابستگی مستقیم دوباره Verify`

در پایان گزارش کوتاه بساز:

`docs/CODEX/FINAL_REPORT.md`

---

# PHASE 14 — GitHub Upload خودکار

این مرحله بعد از اتمام UI و Verification انجام شود.

## Remote policy

`upstream` همیشه Source اصلی باشد:

`https://github.com/smorad3363/Marzban.git`

برای Push:

1. `gh auth status` را بررسی کن.
2. اگر env به نام `TARGET_GITHUB_REPO` وجود دارد، همان را origin قرار بده.
3. اگر وجود ندارد و GitHub CLI authenticated است:
   - username فعلی را از `gh api user` بگیر.
   - Repository خصوصی `Marzban-vNext` را در اکانت همان User پیدا کن.
   - اگر وجود ندارد، خودکار به‌صورت **Private** بساز.
   - آن را `origin` قرار بده.
4. بعد از UI، هر دو branch را Push کن:
   - `vnext-core`
   - `vnext-ui`
5. Tagها را Push کن:
   - `baseline-v5.1.0`
   - `checkpoint-core-complete`
   - `checkpoint-ui-complete`
6. branch نهایی UI را به‌عنوان branch اصلی پروژه جدید تنظیم کن، اگر GitHub اجازه داد.

اگر GitHub authentication وجود ندارد:
- توسعه را متوقف نکن.
- تمام commit/tagها را محلی کامل کن.
- `PUSH_BLOCKED_GITHUB_AUTH` را در STATE و Final Report بنویس.
- Credential نساز و Token حدس نزن.

---

# Rollback فقط UI

اگر بعداً کاربر گفت:

**«UI رو برگردون»**

هیچ Core fix را revert نکن.

مبنای rollback:

`checkpoint-core-complete`

یا branch:

`vnext-core`

فقط تغییرات branch `vnext-ui` حذف/تعویض شوند.

---

# قانون مصرف Token

همیشه:

`STATE → Graphify/rg query → relevant files → official docs only if needed`

هرگز:

`read whole repo → read all history → analyze everything again`

تصمیم‌های قطعی را فقط یک بار در `DECISIONS.md` ذخیره کن.

Architecture summary را کوتاه نگه دار.

Large logs را کامل وارد Context نکن؛ فقط بخش مرتبط را extract کن.

---

# تعریف Done

پروژه وقتی Done است که:

- همه Phaseها اجرا شده باشند
- UI هم بدون توقف بعد Core اجرا شده باشد
- Core و UI جدا rollback شوند
- Verification نهایی موفق باشد یا ریسک باقی‌مانده صریح ثبت شده باشد
- `STATE = COMPLETE`
- Git history واضح باشد
- Core checkpoint و UI checkpoint موجود باشند
- در صورت وجود GitHub auth، هر دو branch و tagها روی GitHub Push شده باشند
