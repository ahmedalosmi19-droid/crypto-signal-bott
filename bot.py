import os
import json
import random
import string

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
TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
DATA_FILE = "products.json"

# ── حالات المحادثة ───────────────────────────────────────────
ASK_NAME, ASK_PRICE, ASK_SIZES, CONFIRM_ADD, ASK_DEL_CODE = range(5)

# ── لوحة مفاتيح المالك ──────────────────────────────────────
OWNER_KB = ReplyKeyboardMarkup(
    [["➕ إضافة سلعة"], ["📋 عرض السلع", "🗑 حذف سلعة"]],
    resize_keyboard=True,
)


# ── قراءة وكتابة البيانات ─────────────────────────────────────
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"products": {}, "retired_codes": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── توليد كود فريد (حرفان + شرطة + 4 رموز)، لا يتكرر أبداً ──
def generate_code(data: dict) -> str:
    blocked = set(data["products"].keys()) | set(data.get("retired_codes", []))
    while True:
        prefix = "".join(random.choices(string.ascii_uppercase, k=2))
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        code = f"{prefix}-{suffix}"
        if code not in blocked:
            return code


# ── مساعد: هل المرسل هو المالك؟ ────────────────────────────
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
    data = load_data()
    if not data["products"]:
        await update.message.reply_text("لا توجد سلع مسجّلة بعد.", reply_markup=OWNER_KB)
        return
    lines = []
    for code, p in data["products"].items():
        sizes = ", ".join(p["sizes"]) if isinstance(p["sizes"], list) else p["sizes"]
        lines.append(f"🏷 {code}\n📦 {p['name']}\n💰 {p['price']}\n📐 {sizes}")
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

    data = load_data()
    code = generate_code(data)
    data["products"][code] = {
        "name": context.user_data["name"],
        "price": context.user_data["price"],
        "sizes": context.user_data["sizes"],
    }
    save_data(data)
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


async def delete_product(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    data = load_data()

    if code not in data["products"]:
        await update.message.reply_text("❌ الكود غير موجود.", reply_markup=OWNER_KB)
        return ConversationHandler.END

    del data["products"][code]
    # احتفظ بالكود في القائمة السوداء — لن يُعاد توليده أبداً
    if "retired_codes" not in data:
        data["retired_codes"] = []
    data["retired_codes"].append(code)
    save_data(data)

    await update.message.reply_text(f"تم حذف السلعة {code} ✅", reply_markup=OWNER_KB)
    return ConversationHandler.END


# ────────────────────────────────────────────────────────────
# /cancel — يعمل داخل أي محادثة
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

# محادثة الإضافة
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

# محادثة الحذف
del_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex(r"^🗑 حذف سلعة$"), delete_start)],
    states={
        ASK_DEL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_product)],
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
