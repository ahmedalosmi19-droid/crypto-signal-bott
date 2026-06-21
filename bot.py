import logging
import os
from datetime import date as _date

import database as db
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ── إعدادات البيئة (يقبل كلا الاسمين) ───────────────────────
TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
assert TOKEN, "يجب ضبط BOT_TOKEN أو TELEGRAM_BOT_TOKEN"
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])

# ── تهيئة قاعدة البيانات ─────────────────────────────────────
logging.basicConfig(level=logging.WARNING)
db.init_db()
db.migrate_from_json("products.json", OWNER_CHAT_ID)  # no-op إن سبق تنفيذها
db.cleanup_admin_shop(OWNER_CHAT_ID)                  # تنظيف لمرة واحدة

# ── تحقق عند بدء التشغيل: هل تعرّف البوت على الأدمن؟ ────────
_env_admin = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()
if _env_admin:
    _admin_ok = db.is_admin(int(_env_admin))
    logging.warning("[STARTUP] ADMIN_TELEGRAM_ID=%s → is_admin=%s", _env_admin, _admin_ok)
else:
    logging.error("[STARTUP] ADMIN_TELEGRAM_ID غير مضبوط في البيئة!")

# ── حالات المحادثة ───────────────────────────────────────────
ASK_NAME, ASK_PRICE, ASK_SIZES, CONFIRM_ADD, ASK_DEL_CODE = range(5)

# ── تسميات المدد للعرض ───────────────────────────────────────
PLAN_LABELS = {
    "biweekly": "أسبوعان",
    "monthly":  "شهر",
    "yearly":   "سنة",
}

# ── لوحات المفاتيح ───────────────────────────────────────────
ADMIN_KB = ReplyKeyboardMarkup(
    [["📊 المشتركون", "📈 إحصاءات المنصّة"]],
    resize_keyboard=True,
)
OWNER_KB = ReplyKeyboardMarkup(
    [["➕ إضافة سلعة"], ["📋 عرض السلع", "🗑 حذف سلعة"]],
    resize_keyboard=True,
)


# ────────────────────────────────────────────────────────────
# مساعدات
# ────────────────────────────────────────────────────────────
def _eff_uid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يُعيد المعرّف الوهمي في وضع الاختبار، وإلا المعرّف الحقيقي"""
    if context.user_data.get("test_mode"):
        return context.user_data["test_shop_id"]
    return update.effective_chat.id


def _clear_conv(context: ContextTypes.DEFAULT_TYPE) -> None:
    """احذف مفاتيح المحادثة فقط، الحفاظ على حالة الاختبار"""
    for key in ("name", "price", "sizes"):
        context.user_data.pop(key, None)


def can_manage(uid: int) -> bool:
    """محل نشط فقط — الأدمن خارج وضع الاختبار لا يملك محلاً"""
    shop = db.get_shop(uid)
    return shop is not None and shop["status"] == "active"


async def _deny_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = _eff_uid(update, context)
    shop = db.get_shop(uid)
    if shop and shop["status"] == "pending":
        await update.message.reply_text("أرسل كود التفعيل أولاً.")
    else:
        await update.message.reply_text("غير مصرّح.")


def _duration_kb(shop_id: int) -> InlineKeyboardMarkup:
    """أزرار اختيار المدة مضمّنة"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("أسبوعان", callback_data=f"dur_biweekly_{shop_id}"),
        InlineKeyboardButton("شهر",    callback_data=f"dur_monthly_{shop_id}"),
        InlineKeyboardButton("سنة",    callback_data=f"dur_yearly_{shop_id}"),
    ]])


# ────────────────────────────────────────────────────────────
# /whoami — تشخيص هوية المُرسِل
# ────────────────────────────────────────────────────────────
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    real_uid  = update.effective_chat.id
    _is_admin = db.is_admin(real_uid)
    _in_test  = context.user_data.get("test_mode", False)
    await update.message.reply_text(
        f"🆔 معرّفك: {real_uid}\n"
        f"👑 أدمن: {'نعم ✅' if _is_admin else 'لا ❌'}\n"
        f"🧪 وضع الاختبار: {'نشط' if _in_test else 'غير نشط'}"
    )


# ────────────────────────────────────────────────────────────
# وضع اختبار المحل
# ────────────────────────────────────────────────────────────
async def testclient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testclient — يدخل الأدمن في وضع محاكاة محل جديد"""
    uid = update.effective_chat.id
    if not db.is_admin(uid):
        return
    test_id = -uid  # معرّف سالب لا يتعارض مع أي حساب حقيقي
    db.clear_test_shop(test_id)  # بدء نظيف في كل جلسة
    context.user_data["test_mode"]    = True
    context.user_data["test_shop_id"] = test_id
    await update.message.reply_text(
        "دخلت وضع اختبار المحل. استعمل /exittest للخروج.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def exittest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/exittest — يخرج من وضع المحاكاة ويعيد كيبورد الأدمن"""
    uid = update.effective_chat.id
    if not db.is_admin(uid):
        return
    context.user_data.pop("test_mode",    None)
    context.user_data.pop("test_shop_id", None)
    await update.message.reply_text("خرجت من وضع الاختبار.", reply_markup=ADMIN_KB)


# ────────────────────────────────────────────────────────────
# /start
# ────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    real_uid = update.effective_chat.id
    username = update.effective_user.username
    in_test  = context.user_data.get("test_mode", False)

    # أدمن خارج وضع الاختبار
    if db.is_admin(real_uid) and not in_test:
        await update.message.reply_text(
            "مرحباً أيها الأدمن.\n"
            "تصلك هنا إشعارات تسجيل المحلات.\n"
            "استعمل /testclient لتجربة واجهة المحل.",
            reply_markup=ADMIN_KB,
        )
        return

    uid  = _eff_uid(update, context)
    shop = db.get_shop(uid)

    if shop is None:
        # محل جديد — سجّله وأرسل إشعاراً للأدمن
        display_name = f"test_{username or real_uid}" if in_test else username
        db.add_shop(uid, display_name)
        await update.message.reply_text(
            "أهلاً بك في المنصّة.\n"
            "لتفعيل حسابك أرسل كود التفعيل الذي ستحصل عليه من الإدارة."
        )
        admin_id = db.get_admin_id()
        if admin_id:
            label = "[اختبار] " if in_test else ""
            await context.bot.send_message(
                admin_id,
                f"🏪 {label}محل جديد سجّل\n"
                f"المعرّف: {uid}\n"
                f"اليوزر: @{username or 'بدون يوزر'}",
                reply_markup=_duration_kb(uid),
            )
        return

    if shop["status"] == "active":
        await update.message.reply_text("مرحباً 👋", reply_markup=OWNER_KB)
    else:
        await update.message.reply_text(
            "حسابك قيد الانتظار.\n"
            "أرسل كود التفعيل الذي ستحصل عليه من الإدارة."
        )


# ────────────────────────────────────────────────────────────
# Callback: تدفّق التفعيل (اختيار مدة → تأكيد → توليد كود)
# ────────────────────────────────────────────────────────────
async def handle_activation_cb(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # الـ callbacks الخاصة بالتفعيل للأدمن فقط
    if not db.is_admin(query.from_user.id):
        return

    data = query.data

    # ── اختيار مدة: dur_biweekly_-123 ──────────────────────
    if data.startswith("dur_"):
        _, plan, shop_id_str = data.split("_", 2)
        shop_id  = int(shop_id_str)
        shop     = db.get_shop(shop_id)
        username = (shop["username"] if shop else None) or "بدون يوزر"
        plan_ar  = PLAN_LABELS.get(plan, plan)

        await query.edit_message_text(
            f"المحل: @{username} ({shop_id})\nالمدة: {plan_ar}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ توليد الكود", callback_data=f"gen_{plan}_{shop_id}"),
                InlineKeyboardButton("↩️ رجوع",       callback_data=f"back_{shop_id}"),
            ]]),
        )

    # ── توليد الكود: gen_monthly_-123 ───────────────────────
    elif data.startswith("gen_"):
        _, plan, shop_id_str = data.split("_", 2)
        shop_id  = int(shop_id_str)
        shop     = db.get_shop(shop_id)
        username = (shop["username"] if shop else None) or "بدون يوزر"

        code = db.create_activation_code(shop_id, plan)
        await query.edit_message_text(
            f"✅ كود التفعيل للمحل @{username} ({shop_id}):\n\n"
            f"{code}\n\n"
            f"أرسل هذا الكود لصاحب المحل.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 إعادة التوليد بمدة أخرى", callback_data=f"back_{shop_id}"),
            ]]),
        )

    # ── رجوع لأزرار المدة: back_-123 ────────────────────────
    elif data.startswith("back_"):
        shop_id  = int(data[5:])
        shop     = db.get_shop(shop_id)
        username = (shop["username"] if shop else None) or "بدون يوزر"
        label    = "[اختبار] " if shop_id < 0 else ""

        await query.edit_message_text(
            f"🏪 {label}محل جديد سجّل\n"
            f"المعرّف: {shop_id}\n"
            f"اليوزر: @{username}",
            reply_markup=_duration_kb(shop_id),
        )


# ────────────────────────────────────────────────────────────
# إرسال كود التفعيل من المحل
# ────────────────────────────────────────────────────────────
async def handle_activation_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = _eff_uid(update, context)
    shop = db.get_shop(uid)

    # ليس محلاً معلّقاً — تصرّف كـ echo عادي
    if shop is None or shop["status"] != "pending":
        await update.message.reply_text(f"أنت كتبت: {update.message.text}")
        return

    code = update.message.text.strip().upper()
    plan = db.redeem_activation_code(code, uid)

    if plan is None:
        await update.message.reply_text("❌ كود غير صالح.")
        return

    shop    = db.get_shop(uid)
    plan_ar = PLAN_LABELS.get(plan, plan)
    await update.message.reply_text(
        f"✅ تم تفعيل اشتراكك ({plan_ar} — ينتهي {shop['end_date']})",
        reply_markup=OWNER_KB,
    )


# ────────────────────────────────────────────────────────────
# أزرار لوحة الأدمن
# ────────────────────────────────────────────────────────────
async def show_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📊 المشتركون — للأدمن خارج وضع الاختبار فقط"""
    uid = update.effective_chat.id
    if not db.is_admin(uid) or context.user_data.get("test_mode"):
        await update.message.reply_text(f"أنت كتبت: {update.message.text}")
        return

    shops = db.get_all_shops()
    if not shops:
        await update.message.reply_text("لا توجد محلات مسجّلة بعد.", reply_markup=ADMIN_KB)
        return

    today = _date.today().isoformat()
    lines = []
    for s in shops:
        end = s["end_date"] or ""
        if s["status"] == "active" and end and end < today:
            badge = "❌ منتهٍ"
        elif s["status"] == "active":
            badge = "✅ نشط"
        else:
            badge = "⏳ منتظر"

        plan_ar  = PLAN_LABELS.get(s["plan"] or "", s["plan"] or "—")
        username = s["username"] or "بدون يوزر"
        lines.append(
            f"{badge} @{username} ({s['telegram_id']})\n"
            f"   الخطة: {plan_ar}  |  ينتهي: {end or '—'}  |  رسائل: {s['message_count']}"
        )

    # إرسال على دفعات لتجنّب حدّ 4096 حرفاً
    MAX = 4000
    chunk = f"📊 المشتركون ({len(shops)}):\n\n"
    for line in lines:
        block = line + "\n\n"
        if len(chunk) + len(block) > MAX:
            await update.message.reply_text(chunk.rstrip(), reply_markup=ADMIN_KB)
            chunk = block
        else:
            chunk += block
    if chunk.strip():
        await update.message.reply_text(chunk.rstrip(), reply_markup=ADMIN_KB)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📈 إحصاءات المنصّة — للأدمن خارج وضع الاختبار فقط"""
    uid = update.effective_chat.id
    if not db.is_admin(uid) or context.user_data.get("test_mode"):
        await update.message.reply_text(f"أنت كتبت: {update.message.text}")
        return

    s = db.get_platform_stats()
    await update.message.reply_text(
        f"📈 إحصاءات المنصّة\n\n"
        f"✅ نشطة:      {s['active']}\n"
        f"❌ منتهية:    {s['expired']}\n"
        f"⏳ منتظرة:    {s['pending']}\n"
        f"👥 الإجمالي:  {s['total']}\n\n"
        f"📦 إجمالي السلع: {s['products']}",
        reply_markup=ADMIN_KB,
    )


# ────────────────────────────────────────────────────────────
# عرض السلع (زر + أمر /list)
# ────────────────────────────────────────────────────────────
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = _eff_uid(update, context)
    if not can_manage(uid):
        await _deny_pending(update, context)
        return
    products = db.get_shop_products(uid)
    if not products:
        await update.message.reply_text("لا توجد سلع مسجّلة بعد.", reply_markup=OWNER_KB)
        return
    lines = []
    for p in products:
        sizes = ", ".join(p["sizes"])
        lines.append(f"🏷 {p['code']}\n📦 {p['name']}\n💰 {p['price']}\n📐 {sizes}")
    await update.message.reply_text("\n\n".join(lines), reply_markup=OWNER_KB)


# ────────────────────────────────────────────────────────────
# إضافة سلعة — ConversationHandler
# ────────────────────────────────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = _eff_uid(update, context)
    if not can_manage(uid):
        await _deny_pending(update, context)
        return ConversationHandler.END
    await update.message.reply_text("اسم السلعة:", reply_markup=ReplyKeyboardRemove())
    return ASK_NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("السعر (رقم فقط):")
    return ASK_PRICE


async def got_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        price = float(text)
    except ValueError:
        await update.message.reply_text("❌ السعر يجب أن يكون رقماً. أعد الإدخال:")
        return ASK_PRICE
    context.user_data["price"] = price
    await update.message.reply_text("القياسات مفصولة بفاصلة (مثال: S,M,L,XL):")
    return ASK_SIZES


async def got_sizes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sizes = [s.strip() for s in update.message.text.split(",") if s.strip()]
    context.user_data["sizes"] = sizes
    name  = context.user_data["name"]
    price = context.user_data["price"]
    summary = (
        f"📋 ملخص السلعة:\n"
        f"📦 الاسم: {name}\n"
        f"💰 السعر: {price}\n"
        f"📐 القياسات: {', '.join(sizes)}\n\n"
        "تأكيد الحفظ؟"
    )
    confirm_kb = ReplyKeyboardMarkup(
        [["✅ نعم", "❌ لا"]], resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(summary, reply_markup=confirm_kb)
    return CONFIRM_ADD


async def confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "نعم" not in update.message.text.strip():
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=OWNER_KB)
        _clear_conv(context)
        return ConversationHandler.END

    uid  = _eff_uid(update, context)
    code = db.generate_unique_code()
    db.add_product(
        code, uid,
        context.user_data["name"],
        context.user_data["price"],
        context.user_data["sizes"],
    )
    _clear_conv(context)

    await update.message.reply_text(
        f"تمت الإضافة ✅ — ضع هذا الكود في آخر كابشن منشور السلعة على إنستغرام: {code}",
        reply_markup=OWNER_KB,
    )
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────
# حذف سلعة — ConversationHandler
# ────────────────────────────────────────────────────────────
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = _eff_uid(update, context)
    if not can_manage(uid):
        await _deny_pending(update, context)
        return ConversationHandler.END
    await update.message.reply_text("أرسل كود السلعة المراد حذفها:", reply_markup=ReplyKeyboardRemove())
    return ASK_DEL_CODE


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = _eff_uid(update, context)
    code    = update.message.text.strip().upper()
    deleted = db.delete_product(code, uid)
    if not deleted:
        await update.message.reply_text("❌ الكود غير موجود أو لا يخصّك.", reply_markup=OWNER_KB)
        return ConversationHandler.END
    await update.message.reply_text(f"تم حذف السلعة {code} ✅", reply_markup=OWNER_KB)
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────
# /cancel
# ────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_conv(context)
    await update.message.reply_text("تم الإلغاء.", reply_markup=OWNER_KB)
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────
# منطق الزبون الحالي (لا يُمس)
# ────────────────────────────────────────────────────────────
async def echo(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"أنت كتبت: {update.message.text}")


# ────────────────────────────────────────────────────────────
# تجميع البوت
# ────────────────────────────────────────────────────────────
app = ApplicationBuilder().token(TOKEN).build()

add_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex(r"^➕ إضافة سلعة$"), add_start)],
    states={
        ASK_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
        ASK_PRICE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_price)],
        ASK_SIZES:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_sizes)],
        CONFIRM_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_save)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

del_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex(r"^🗑 حذف سلعة$"), delete_start)],
    states={
        ASK_DEL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delete)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

app.add_handler(CommandHandler("start",      start))
app.add_handler(CommandHandler("list",       list_products))
app.add_handler(CommandHandler("testclient", testclient))
app.add_handler(CommandHandler("exittest",   exittest))
app.add_handler(CommandHandler("whoami",     whoami))
# callbacks التفعيل: dur_ / gen_ / back_
app.add_handler(CallbackQueryHandler(handle_activation_cb, pattern=r"^(dur|gen|back)_"))
app.add_handler(add_conv)
app.add_handler(del_conv)
app.add_handler(MessageHandler(filters.Regex(r"^📋 عرض السلع$"),             list_products))
app.add_handler(MessageHandler(filters.Regex(r"^📊 المشتركون$"),       show_subscribers))
app.add_handler(MessageHandler(filters.Regex(r"^📈 إحصاءات المنصّة$"), show_stats))
# كود التفعيل يُعالَج قبل echo
app.add_handler(MessageHandler(filters.Regex(r"^ACT-[A-Z0-9]{5}$"),          handle_activation_code))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,               echo))

print("Bot is running...")
app.run_polling()
