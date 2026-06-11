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

# ── إعدادات البيئة ──────────────────────────────────────────
TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])

# ── تهيئة قاعدة البيانات والترحيل ───────────────────────────
db.init_db()
db.migrate_from_json("products.json", OWNER_CHAT_ID)
db.add_shop(OWNER_CHAT_ID)
db.set_shop_active_unlimited(OWNER_CHAT_ID)  # الأدمن نشط دائماً للاختبار

# ── حالات المحادثة ───────────────────────────────────────────
ASK_NAME, ASK_PRICE, ASK_SIZES, CONFIRM_ADD, ASK_DEL_CODE = range(5)

# ── لوحة مفاتيح المحل النشط ──────────────────────────────────
OWNER_KB = ReplyKeyboardMarkup(
    [["➕ إضافة سلعة"], ["📋 عرض السلع", "🗑 حذف سلعة"]],
    resize_keyboard=True,
)


# ── مساعد: هل يمكن لهذا المستخدم إدارة سلعه؟ ────────────────
def can_manage(uid: int) -> bool:
    """أدمن أو محل نشط"""
    if db.is_admin(uid):
        return True
    shop = db.get_shop(uid)
    return shop is not None and shop["status"] == "active"


async def _deny_pending(update: Update) -> None:
    """رد على المحل المعلّق بطلب التفعيل"""
    shop = db.get_shop(update.effective_chat.id)
    if shop and shop["status"] == "pending":
        await update.message.reply_text("أرسل كود التفعيل أولاً.")
    else:
        await update.message.reply_text("غير مصرّح.")


# ────────────────────────────────────────────────────────────
# /start
# ────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    username = update.effective_user.username

    # أدمن
    if db.is_admin(uid):
        await update.message.reply_text("مرحباً يا أدمن 👑", reply_markup=OWNER_KB)
        return

    shop = db.get_shop(uid)

    if shop is None:
        # مستخدم جديد — سجّله وأبلّغ الأدمن
        db.add_shop(uid, username)
        await update.message.reply_text(
            "أهلاً بك في المنصّة.\n"
            "لتفعيل حسابك أرسل كود التفعيل الذي ستحصل عليه من الإدارة."
        )
        admin_id = db.get_admin_id()
        if admin_id:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("تفعيل شهري",   callback_data=f"act_monthly_{uid}"),
                InlineKeyboardButton("تفعيل أسبوعي", callback_data=f"act_weekly_{uid}"),
            ]])
            await context.bot.send_message(
                admin_id,
                f"🏪 محل جديد سجّل\n"
                f"المعرّف: {uid}\n"
                f"اليوزر: @{username or 'بدون يوزر'}",
                reply_markup=kb,
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
# ضغط الأدمن على زر التفعيل → توليد الكود
# ────────────────────────────────────────────────────────────
async def handle_activation_button(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not db.is_admin(query.from_user.id):
        return

    # callback_data: act_monthly_123456  أو  act_weekly_123456
    parts = query.data.split("_")          # ['act', 'monthly'/'weekly', '<id>']
    plan = parts[1]
    shop_id = int(parts[2])

    code = db.create_activation_code(shop_id, plan)
    plan_ar = "شهري" if plan == "monthly" else "أسبوعي"

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ كود التفعيل ({plan_ar}) للمحل {shop_id}:\n\n"
        f"{code}\n\n"
        f"أرسل هذا الكود لصاحب المحل."
    )


# ────────────────────────────────────────────────────────────
# إرسال المحل كود التفعيل
# ────────────────────────────────────────────────────────────
async def handle_activation_code(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    shop = db.get_shop(uid)

    # إن لم يكن محلاً معلّقاً، تصرّف كـ echo عادي
    if shop is None or shop["status"] != "pending":
        await update.message.reply_text(f"أنت كتبت: {update.message.text}")
        return

    code = update.message.text.strip().upper()
    plan = db.redeem_activation_code(code, uid)

    if plan is None:
        await update.message.reply_text("❌ كود غير صالح.")
        return

    shop = db.get_shop(uid)
    plan_ar = "شهري" if plan == "monthly" else "أسبوعي"
    await update.message.reply_text(
        f"✅ تم تفعيل اشتراكك ({plan_ar} — ينتهي {shop['end_date']})",
        reply_markup=OWNER_KB,
    )


# ────────────────────────────────────────────────────────────
# عرض السلع (زر + أمر /list)
# ────────────────────────────────────────────────────────────
async def list_products(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if not can_manage(uid):
        await _deny_pending(update)
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
async def add_start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if not can_manage(uid):
        await _deny_pending(update)
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
        context.user_data.clear()
        return ConversationHandler.END

    uid  = update.effective_chat.id
    code = db.generate_unique_code()
    db.add_product(
        code, uid,
        context.user_data["name"],
        context.user_data["price"],
        context.user_data["sizes"],
    )
    context.user_data.clear()

    await update.message.reply_text(
        f"تمت الإضافة ✅ — ضع هذا الكود في آخر كابشن منشور السلعة على إنستغرام: {code}",
        reply_markup=OWNER_KB,
    )
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────
# حذف سلعة — ConversationHandler
# ────────────────────────────────────────────────────────────
async def delete_start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if not can_manage(uid):
        await _deny_pending(update)
        return ConversationHandler.END
    await update.message.reply_text("أرسل كود السلعة المراد حذفها:", reply_markup=ReplyKeyboardRemove())
    return ASK_DEL_CODE


async def handle_delete(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_chat.id
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
    context.user_data.clear()
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

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("list", list_products))
app.add_handler(CallbackQueryHandler(handle_activation_button, pattern=r"^act_(monthly|weekly)_\d+$"))
app.add_handler(add_conv)
app.add_handler(del_conv)
app.add_handler(MessageHandler(filters.Regex(r"^📋 عرض السلع$"), list_products))
# كود التفعيل يُعالَج قبل echo
app.add_handler(MessageHandler(filters.Regex(r"^ACT-[A-Z0-9]{5}$"), handle_activation_code))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

print("Bot is running...")
app.run_polling()
