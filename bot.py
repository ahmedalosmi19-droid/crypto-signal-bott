import os
import re
import logging

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

# ── السجل (logging) ─────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── تهيئة قاعدة البيانات ─────────────────────────────────────
db.init_db()
db.migrate_from_json("products.json", OWNER_CHAT_ID)  # no-op إن سبق تنفيذها
db.cleanup_admin_shop(OWNER_CHAT_ID)                  # تنظيف لمرة واحدة

# تحقق عند بدء التشغيل: هل تعرّف البوت على الأدمن؟
_env_admin = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()
if _env_admin:
    logger.info("[STARTUP] ADMIN_TELEGRAM_ID=%s → is_admin=%s",
                _env_admin, db.is_admin(int(_env_admin)))
else:
    logger.error("[STARTUP] ADMIN_TELEGRAM_ID غير مضبوط في البيئة!")

# ── حالات محادثات الإضافة/الحذف ─────────────────────────────
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

# نمط كود التفعيل (غير حساس لحالة الأحرف)
ACT_RE = re.compile(r"^ACT-[A-Za-z0-9]{5}$")


# ────────────────────────────────────────────────────────────
# مساعدات
# ────────────────────────────────────────────────────────────
def _eff_uid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """المعرّف الوهمي في وضع الاختبار، وإلا المعرّف الحقيقي"""
    if context.user_data.get("test_mode"):
        return context.user_data["test_shop_id"]
    return update.effective_chat.id


def _clear_conv(context: ContextTypes.DEFAULT_TYPE) -> None:
    """احذف مفاتيح المحادثة فقط مع الحفاظ على حالة الاختبار"""
    for key in ("name", "price", "sizes"):
        context.user_data.pop(key, None)


def can_manage(uid: int) -> bool:
    """محل نشط فقط — الأدمن خارج وضع الاختبار لا يملك محلاً"""
    shop = db.get_shop(uid)
    return shop is not None and shop["status"] == "active"


def _kb_for(uid: int, context: ContextTypes.DEFAULT_TYPE) -> ReplyKeyboardMarkup:
    """اختر الكيبورد المناسب: أدمن (خارج الاختبار) أم محل"""
    if db.is_admin(uid) and not context.user_data.get("test_mode"):
        return ADMIN_KB
    return OWNER_KB


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
    db.clear_test_shop(test_id)
    context.user_data["test_mode"]    = True
    context.user_data["test_shop_id"] = test_id
    await update.message.reply_text(
        "🧪 دخلت وضع اختبار المحل. أرسل /start لتبدأ كمحل جديد، و/exittest للخروج.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def exittest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/exittest — يخرج من وضع المحاكاة ويعيد كيبورد الأدمن"""
    uid = update.effective_chat.id
    if not db.is_admin(uid):
        return
    context.user_data.pop("test_mode",    None)
    context.user_data.pop("test_shop_id", None)
    _clear_conv(context)
    await update.message.reply_text("↩️ خرجت من وضع الاختبار.", reply_markup=ADMIN_KB)


# ────────────────────────────────────────────────────────────
# /start
# ────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    real_uid = update.effective_chat.id
    username = update.effective_user.username
    in_test  = context.user_data.get("test_mode", False)

    # أدمن خارج وضع الاختبار → كيبورد الأدمن
    if db.is_admin(real_uid) and not in_test:
        await update.message.reply_text(
            "مرحباً أيها الأدمن 👑\n"
            "تصلك هنا إشعارات تسجيل المحلات الجديدة.\n"
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
            "أهلاً بك في المنصّة 👋\n"
            "لتفعيل حسابك أرسل كود التفعيل الذي ستحصل عليه من الإدارة."
        )
        admin_id = db.get_admin_id()
        if admin_id:
            label = "[اختبار] " if in_test else ""
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🏪 {label}محل جديد سجّل\n"
                    f"المعرّف: {uid}\n"
                    f"اليوزر: @{username or 'بدون يوزر'}\n\n"
                    f"اختر مدة الاشتراك:",
                    reply_markup=_duration_kb(uid),
                )
            except Exception as e:
                logger.error("فشل إرسال إشعار الأدمن: %s", e)
        return

    if shop["status"] == "active":
        await update.message.reply_text("مرحباً 👋", reply_markup=OWNER_KB)
    else:
        await update.message.reply_text(
            "حسابك قيد الانتظار.\n"
            "أرسل كود التفعيل الذي ستحصل عليه من الإدارة."
        )


# ────────────────────────────────────────────────────────────
# Callback: تدفّق التفعيل (مدة → ملخص → توليد كود → رجوع)
# ────────────────────────────────────────────────────────────
async def handle_activation_cb(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not db.is_admin(query.from_user.id):
        return

    data = query.data

    # اختيار مدة → ملخص بزرّي توليد/رجوع
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

    # توليد الكود
    elif data.startswith("gen_"):
        _, plan, shop_id_str = data.split("_", 2)
        shop_id  = int(shop_id_str)
        shop     = db.get_shop(shop_id)
        username = (shop["username"] if shop else None) or "بدون يوزر"
        code     = db.create_activation_code(shop_id, plan)
        await query.edit_message_text(
            f"✅ كود التفعيل للمحل @{username} ({shop_id}):\n\n"
            f"{code}\n\n"
            f"أرسل هذا الكود لصاحب المحل.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 إعادة التوليد بمدة أخرى", callback_data=f"back_{shop_id}"),
            ]]),
        )

    # رجوع لأزرار المدد
    elif data.startswith("back_"):
        shop_id  = int(data[5:])
        shop     = db.get_shop(shop_id)
        username = (shop["username"] if shop else None) or "بدون يوزر"
        label    = "[اختبار] " if shop_id < 0 else ""
        await query.edit_message_text(
            f"🏪 {label}محل جديد سجّل\n"
            f"المعرّف: {shop_id}\n"
            f"اليوزر: @{username}\n\n"
            f"اختر مدة الاشتراك:",
            reply_markup=_duration_kb(shop_id),
        )


# ────────────────────────────────────────────────────────────
# إرسال المحل كود التفعيل
# ────────────────────────────────────────────────────────────
async def handle_activation_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = _eff_uid(update, context)
    shop = db.get_shop(uid)

    # إن لم يكن محلاً معلّقاً → مرّر للمعالجة العامة (echo/أدمن)
    if shop is None or shop["status"] != "pending":
        await fallback_text(update, context)
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
# عرض السلع
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
# أزرار الأدمن الشكلية (قيد البناء)
# ────────────────────────────────────────────────────────────
async def admin_stub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if db.is_admin(uid) and not context.user_data.get("test_mode"):
        await update.message.reply_text("هذه الميزة قيد البناء.", reply_markup=ADMIN_KB)
    else:
        await fallback_text(update, context)


# ────────────────────────────────────────────────────────────
# النص العام (آخر معالج): كود تفعيل، أو رد افتراضي
# ────────────────────────────────────────────────────────────
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    # كود تفعيل؟
    if ACT_RE.match(text):
        await handle_activation_code(update, context)
        return
    await fallback_text(update, context)


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رد افتراضي لأي نص غير مفهوم (لاحقاً: منطق الزبون)"""
    await update.message.reply_text("لم أفهم رسالتك. استعمل الأزرار أو /start.")


# ────────────────────────────────────────────────────────────
# معالج الأخطاء العام
# ────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("استثناء أثناء معالجة تحديث:", exc_info=context.error)


# ────────────────────────────────────────────────────────────
# تجميع البوت
# ────────────────────────────────────────────────────────────
def main():
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

    # الأوامر
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("whoami",     whoami))
    app.add_handler(CommandHandler("testclient", testclient))
    app.add_handler(CommandHandler("exittest",   exittest))
    app.add_handler(CommandHandler("list",       list_products))

    # أزرار التفعيل المضمّنة
    app.add_handler(CallbackQueryHandler(handle_activation_cb, pattern=r"^(dur|gen|back)_"))

    # محادثات السلع
    app.add_handler(add_conv)
    app.add_handler(del_conv)

    # أزرار الكيبورد
    app.add_handler(MessageHandler(filters.Regex(r"^📋 عرض السلع$"), list_products))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 المشتركون$|^📈 إحصاءات المنصّة$"), admin_stub))

    # النص العام (آخر معالج)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # الأخطاء
    app.add_error_handler(error_handler)

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
