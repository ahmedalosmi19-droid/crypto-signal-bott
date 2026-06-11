
import os
 
import database as db
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
 
# ── إعدادات البيئة ──────────────────────────────────────────
# يقبل أياً من الاسمين حتى لا ينهار لو اختلف اسم المتغيّر في Railway
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
 
# ── تهيئة قاعدة البيانات والترحيل ───────────────────────────
db.init_db()
db.migrate_from_json("products.json", OWNER_CHAT_ID)
db.add_shop(OWNER_CHAT_ID)
 
# ── حالات المحادثة ───────────────────────────────────────────
ASK_NAME, ASK_PRICE, ASK_SIZES, CONFIRM_ADD, ASK_DEL_CODE = range(5)
 
# ── لوحة مفاتيح المالك ──────────────────────────────────────
OWNER_KB = ReplyKeyboardMarkup(
    [["➕ إضافة سلعة"], ["📋 عرض السلع", "🗑 حذف سلعة"]],
    resize_keyboard=True,
)
 
 
def is_owner(update: Update) -> bool:
    return update.effective_chat.id == OWNER_CHAT_ID
 
 
# ────────────────────────────────────────────────────────────
# /start
# ────────────────────────────────────────────────────────────
async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update):
        await update.message.reply_text("مرحباً يا صاحب المحل 👋", reply_markup=OWNER_KB)
    else:
        await update.message.reply_text("البوت شغال.")
 
 
# ────────────────────────────────────────────────────────────
# عرض السلع (زر + أمر /list)
# ────────────────────────────────────────────────────────────
async def list_products(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    products = db.get_shop_products(OWNER_CHAT_ID)
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
    if not is_owner(update):
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
    name = context.user_data["name"]
    price = context.user_data["price"]
    summary = (
        f"📋 ملخص السلعة:\n"
        f"📦 الاسم: {name}\n"
        f"💰 السعر: {price}\n"
        f"📐 القياسات: {', '.join(sizes)}\n\n"
        "تأكيد الحفظ؟"
    )
    confirm_kb = ReplyKeyboardMarkup([["✅ نعم", "❌ لا"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(summary, reply_markup=confirm_kb)
    return CONFIRM_ADD
 
 
async def confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip()
    if "نعم" not in answer:
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=OWNER_KB)
        context.user_data.clear()
        return ConversationHandler.END
 
    code = db.generate_unique_code()
    db.add_product(
        code,
        OWNER_CHAT_ID,
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
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("أرسل كود السلعة المراد حذفها:", reply_markup=ReplyKeyboardRemove())
    return ASK_DEL_CODE
 
 
async def handle_delete(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    deleted = db.delete_product(code)
    if not deleted:
        await update.message.reply_text("❌ الكود غير موجود.", reply_markup=OWNER_KB)
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
    user_text = update.message.text
    await update.message.reply_text(f"أنت كتبت: {user_text}")
 
 
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
app.add_handler(add_conv)
app.add_handler(del_conv)
app.add_handler(MessageHandler(filters.Regex(r"^📋 عرض السلع$"), list_products))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
 
print("Bot is running...")
app.run_polling()
