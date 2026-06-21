import logging
import os

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
