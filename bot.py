"""
بوت تيليجرام لإدارة متجر إلكتروني (نواة المرحلة الأولى — محل واحد).
ملف واحد كامل: النموذج + التخزين + المطابقة + المعالجات + التشغيل.
 
الإعداد قبل التشغيل:
    1) احصل على التوكن من @BotFather عبر الأمر /newbot
    2) احصل على معرّفك الرقمي من @userinfobot
    3) اضبط متغيّري البيئة:
         Windows (PowerShell):
           $env:TELEGRAM_BOT_TOKEN = "توكنك_هنا"
           $env:OWNER_CHAT_ID      = "معرفك_هنا"
         Linux/Mac:
           export TELEGRAM_BOT_TOKEN="توكنك_هنا"
           export OWNER_CHAT_ID="معرفك_هنا"
    4) ثبّت المكتبة:  pip install python-telegram-bot==21.6
    5) شغّل:          python bot.py
 
ملاحظة: يُنشأ ملف products.json تلقائياً في أول تشغيل.
"""
 
import os
import re
import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple
 
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters,
)
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)
 
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))
PRODUCTS_FILE = "products.json"
 
 
# ============================================================
#  1) نموذج البيانات
# ============================================================
@dataclass
class Product:
    id: str
    name: str
    price: float
    sizes: List[str]
    stock: int
    description: str
 
 
# ============================================================
#  2) طبقة التخزين (معزولة — يسهل استبدالها بقاعدة بيانات لاحقاً)
# ============================================================
class ProductRepository:
    def __init__(self, filepath: str = PRODUCTS_FILE):
        self.filepath = filepath
        if not os.path.exists(self.filepath):
            self._save([])
 
    def _load(self) -> list:
        with open(self.filepath, "r", encoding="utf-8") as f:
            return json.load(f)
 
    def _save(self, data: list):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
 
    def get_all(self) -> List[Product]:
        return [Product(**item) for item in self._load()]
 
    def add(self, product: Product) -> bool:
        data = self._load()
        if any(item["id"].lower() == product.id.lower() for item in data):
            return False
        data.append(asdict(product))
        self._save(data)
        return True
 
    def delete(self, product_id: str) -> bool:
        data = self._load()
        new_data = [i for i in data if i["id"].lower() != product_id.lower()]
        if len(new_data) == len(data):
            return False
        self._save(new_data)
        return True
 
 
repo = ProductRepository()
 
 
# ============================================================
#  3) منطق المطابقة والفهم
# ============================================================
CODE_PATTERN = re.compile(r"\b[A-Za-z]{1,5}-\d{2,4}\b")
 
# نية شراء صريحة فقط — لا كلمات استفهام عامة (كيف/متى/عندكم...)
PURCHASE_KEYWORDS = [
    "اشتري", "أشتري", "اشتريه", "اطلب", "أطلب", "اطلبه", "اطلبية",
    "اوردر", "أوردر", "احجز", "أحجز", "احجزه", "order", "buy", "purchase",
]
 
 
def find_product(text: str, products: List[Product]) -> Tuple[Optional[Product], Optional[str]]:
    """
    يعيد (المنتج، نوع التطابق):
      "code" = طابق كوداً صريحاً مثل SH-001 (الأدق)
      "text" = طابق باسم المنتج
      (None, None) = لا تطابق
    """
    # 1) مطابقة الكود الصريح أولاً
    for match in CODE_PATTERN.findall(text):
        for p in products:
            if p.id.lower() == match.lower():
                return p, "code"
 
    # 2) مطابقة نصية بالاسم
    text_lower = text.lower()
    best, best_score = None, 0
    for p in products:
        score = sum(1 for word in p.name.lower().split() if word in text_lower)
        if score > best_score:
            best_score, best = score, p
 
    if best_score >= 1:
        return best, "text"
    return None, None
 
 
def has_purchase_intent(text: str) -> bool:
    """نية شراء صريحة فقط؛ أسئلة الأسعار العادية ليست نية شراء."""
    t = text.lower()
    return any(kw in t for kw in PURCHASE_KEYWORDS)
 
 
# ============================================================
#  4) المعالجات (Handlers)
# ============================================================
ADD_ID, ADD_NAME, ADD_PRICE, ADD_SIZES, ADD_STOCK, ADD_DESC = range(6)
 
 
def _is_owner(update: Update) -> bool:
    return update.effective_user.id == OWNER_CHAT_ID
 
 
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً! أرسل اسم منتج أو كوده وسأردّ عليك فوراً.")
 
 
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    products = repo.get_all()
    if not products:
        await update.message.reply_text("لا توجد منتجات.")
        return
    lines = []
    for p in products:
        status = "✅" if p.stock > 0 else "❌"
        lines.append(f"{status} {p.id} — {p.name} | {p.price} | مخزون: {p.stock}")
    await update.message.reply_text("\n".join(lines))
 
 
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /delete SH-001")
        return
    code = context.args[0]
    if repo.delete(code):
        await update.message.reply_text(f"✅ تم حذف {code}")
    else:
        await update.message.reply_text(f"لم أجد منتجاً بالكود {code}")
 
 
# --- حوار إضافة منتج (لصاحب المحل) ---
async def cmd_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("أدخل كود المنتج (مثال: SH-001):")
    return ADD_ID
 
async def add_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["np"] = {"id": update.message.text.strip()}
    await update.message.reply_text("أدخل اسم المنتج:")
    return ADD_NAME
 
async def add_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["np"]["name"] = update.message.text.strip()
    await update.message.reply_text("أدخل السعر (رقم):")
    return ADD_PRICE
 
async def add_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["np"]["price"] = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("أدخل رقماً صحيحاً:")
        return ADD_PRICE
    await update.message.reply_text("أدخل المقاسات مفصولة بفاصلة (مثال: S,M,L,XL):")
    return ADD_SIZES
 
async def add_get_sizes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["np"]["sizes"] = [s.strip() for s in update.message.text.split(",") if s.strip()]
    await update.message.reply_text("أدخل الكمية المتوفرة:")
    return ADD_STOCK
 
async def add_get_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["np"]["stock"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("أدخل عدداً صحيحاً:")
        return ADD_STOCK
    await update.message.reply_text("أدخل وصف المنتج:")
    return ADD_DESC
 
async def add_get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["np"]["description"] = update.message.text.strip()
    product = Product(**context.user_data.pop("np"))
    if repo.add(product):
        await update.message.reply_text(f"✅ تمت إضافة {product.name}")
    else:
        await update.message.reply_text(f"الكود {product.id} مستخدم مسبقاً.")
    return ConversationHandler.END
 
async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("np", None)
    await update.message.reply_text("تم إلغاء الإضافة.")
    return ConversationHandler.END
 
 
# --- رسائل الزبائن ---
def _format_product_reply(product: Product) -> str:
    sizes_str = "، ".join(product.sizes) if product.sizes else "—"
    status = "✅ متوفر" if product.stock > 0 else "❌ نفد المخزون"
    return (
        f"{product.name}\n"
        f"الكود: {product.id}\n"
        f"السعر: {product.price}\n"
        f"المقاسات: {sizes_str}\n"
        f"الحالة: {status}\n\n"
        f"{product.description}"
    )
 
 
async def _alert_owner(context: ContextTypes.DEFAULT_TYPE, update: Update,
                       product: Optional[Product], reason: str):
    user = update.effective_user
    text = update.message.text or ""
    product_line = f"\nالمنتج المرجّح: {product.name} ({product.id})" if product else ""
    alert = (
        f"🔔 رسالة تحتاج ردك ({reason})\n"
        f"من: {user.full_name} (@{user.username or '—'}) | ID: {user.id}"
        f"{product_line}\n\n"
        f"{text}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=alert)
    except Exception as e:
        logger.error(f"فشل إرسال التنبيه: {e}")
 
 
async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER_CHAT_ID:
        return
 
    text = update.message.text or ""
    product, _match_type = find_product(text, repo.get_all())
 
    # المنطق المصحّح:
    #   ينبّه صاحب المحل فقط إذا (لم يُعرف المنتج) أو (وُجدت نية شراء صريحة).
    #   خلاف ذلك يرد بالسعر تلقائياً مهما كانت كلمات السؤال (عندكم؟ كيف؟ ...).
    if product is None:
        await _alert_owner(context, update, None, "لم يُعرف المنتج")
        return
 
    if has_purchase_intent(text):
        await _alert_owner(context, update, product, "نية شراء")
        return
 
    await update.message.reply_text(_format_product_reply(product))
 
 
# ============================================================
#  5) التشغيل
# ============================================================
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN غير مضبوط")
    if OWNER_CHAT_ID == 0:
        logger.warning("OWNER_CHAT_ID غير مضبوط — أوامر صاحب المحل والتنبيهات لن تعمل.")
 
    app = ApplicationBuilder().token(token).build()
 
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add_start)],
        states={
            ADD_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_id)],
            ADD_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_price)],
            ADD_SIZES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_sizes)],
            ADD_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_stock)],
            ADD_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_desc)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )
 
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(add_conv)
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_customer_message))
 
    logger.info("البوت يعمل...")
    app.run_polling()
 
 
if __name__ == "__main__":
    main()
