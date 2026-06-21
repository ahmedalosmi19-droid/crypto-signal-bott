import os
import json
import logging
import sqlite3
import random
import string
from contextlib import contextmanager
from typing import Optional

DB_FILE = "bot.db"

# ── مدد الاشتراك بالأيام — عدّلها هنا فقط ────────────────────
PLAN_DAYS = {
    "biweekly": 14,
    "monthly":  30,
    "yearly":   365,
}


@contextmanager
def _conn():
    """مدير سياق للاتصال بقاعدة البيانات"""
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ────────────────────────────────────────────────────────────
# تهيئة الجداول
# ────────────────────────────────────────────────────────────
def init_db() -> None:
    """أنشئ الجداول عند أول تشغيل وسجّل الأدمن"""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS shops (
                telegram_id   INTEGER PRIMARY KEY,
                username      TEXT,
                status        TEXT    NOT NULL DEFAULT 'pending',
                plan          TEXT,
                start_date    TEXT,
                end_date      TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                joined_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS products (
                code    TEXT    PRIMARY KEY,
                shop_id INTEGER NOT NULL REFERENCES shops(telegram_id),
                name    TEXT    NOT NULL,
                price   REAL    NOT NULL,
                sizes   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS retired_codes (
                code TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS admin (
                telegram_id INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS activation_codes (
                code    TEXT    PRIMARY KEY,
                shop_id INTEGER NOT NULL REFERENCES shops(telegram_id),
                plan    TEXT    NOT NULL,
                used    INTEGER NOT NULL DEFAULT 0
            );
        """)

        admin_id = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()
        if admin_id:
            con.execute(
                "INSERT OR IGNORE INTO admin (telegram_id) VALUES (?)",
                (int(admin_id),)
            )
            registered = con.execute(
                "SELECT telegram_id FROM admin WHERE telegram_id = ?", (int(admin_id),)
            ).fetchone()
            if registered:
                logging.warning("[ADMIN] OK ADMIN_TELEGRAM_ID=%s مسجّل في جدول admin", admin_id)
            else:
                logging.error("[ADMIN] FAIL ADMIN_TELEGRAM_ID=%s فشل التسجيل!", admin_id)
        else:
            logging.error("[ADMIN] ADMIN_TELEGRAM_ID غير مضبوط في متغيرات البيئة!")


# ── الأدمن ──────────────────────────────────────────────────
def is_admin(telegram_id: int) -> bool:
    with _conn() as con:
        return con.execute(
            "SELECT 1 FROM admin WHERE telegram_id = ?", (telegram_id,)
        ).fetchone() is not None


def get_admin_id() -> Optional[int]:
    with _conn() as con:
        row = con.execute("SELECT telegram_id FROM admin LIMIT 1").fetchone()
        return row[0] if row else None


# ── المحلات ─────────────────────────────────────────────────
def add_shop(telegram_id: int, username: Optional[str] = None) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO shops (telegram_id, username) VALUES (?, ?)",
            (telegram_id, username)
        )


def get_shop(telegram_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM shops WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return dict(row) if row else None


def cleanup_admin_shop(admin_id: int) -> None:
    """احذف بيانات المحل القديمة للأدمن (تنظيف لمرة واحدة عند التحويل)"""
    with _conn() as con:
        rows = con.execute(
            "SELECT code FROM products WHERE shop_id = ?", (admin_id,)
        ).fetchall()
        for row in rows:
            con.execute(
                "INSERT OR IGNORE INTO retired_codes (code) VALUES (?)", (row[0],)
            )
        con.execute("DELETE FROM products WHERE shop_id = ?", (admin_id,))
        con.execute("DELETE FROM shops WHERE telegram_id = ?", (admin_id,))


def clear_test_shop(test_id: int) -> None:
    """امسح بيانات محل الاختبار لبدء نظيف في كل جلسة"""
    with _conn() as con:
        con.execute("DELETE FROM products WHERE shop_id = ?", (test_id,))
        con.execute("DELETE FROM activation_codes WHERE shop_id = ?", (test_id,))
        con.execute("DELETE FROM shops WHERE telegram_id = ?", (test_id,))


def set_shop_active_unlimited(telegram_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE shops SET status='active', plan='admin', start_date=date('now') "
            "WHERE telegram_id = ?",
            (telegram_id,)
        )


def increment_message_count(shop_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE shops SET message_count = message_count + 1 WHERE telegram_id = ?",
            (shop_id,)
        )


# ── أكواد التفعيل ───────────────────────────────────────────
def create_activation_code(shop_id: int, plan: str) -> str:
    with _conn() as con:
        while True:
            suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
            code = f"ACT-{suffix}"
            exists = con.execute(
                "SELECT 1 FROM activation_codes WHERE code = ?", (code,)
            ).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO activation_codes (code, shop_id, plan) VALUES (?, ?, ?)",
                    (code, shop_id, plan)
                )
                return code


def redeem_activation_code(code: str, shop_id: int) -> Optional[str]:
    """تحقق من كود التفعيل وفعّل المحل. يُعيد اسم الخطة أو None إن فشل."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM activation_codes WHERE code = ? AND shop_id = ? AND used = 0",
            (code, shop_id)
        ).fetchone()
        if not row:
            return None
        plan = row["plan"]
        days = PLAN_DAYS.get(plan, 30)
        con.execute(
            """UPDATE shops
               SET status='active', plan=?,
                   start_date=date('now'),
                   end_date=date('now', ?)
               WHERE telegram_id = ?""",
            (plan, f"+{days} days", shop_id)
        )
        con.execute("UPDATE activation_codes SET used=1 WHERE code=?", (code,))
        return plan


# ── السلع ───────────────────────────────────────────────────
def add_product(code: str, shop_id: int, name: str, price: float, sizes: list) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO products (code, shop_id, name, price, sizes) VALUES (?, ?, ?, ?, ?)",
            (code, shop_id, name, price, ",".join(sizes))
        )


def get_product(code: str) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM products WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return None
        p = dict(row)
        p["sizes"] = p["sizes"].split(",")
        return p


def get_shop_products(shop_id: int) -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM products WHERE shop_id = ?", (shop_id,)
        ).fetchall()
        result = []
        for row in rows:
            p = dict(row)
            p["sizes"] = p["sizes"].split(",")
            result.append(p)
        return result


def delete_product(code: str, shop_id: int) -> bool:
    with _conn() as con:
        affected = con.execute(
            "DELETE FROM products WHERE code = ? AND shop_id = ?", (code, shop_id)
        ).rowcount
        if affected:
            con.execute(
                "INSERT OR IGNORE INTO retired_codes (code) VALUES (?)", (code,)
            )
        return bool(affected)


def generate_unique_code() -> str:
    with _conn() as con:
        while True:
            prefix = "".join(random.choices(string.ascii_uppercase, k=2))
            suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            code = f"{prefix}-{suffix}"
            exists = con.execute(
                "SELECT 1 FROM products WHERE code = ? "
                "UNION SELECT 1 FROM retired_codes WHERE code = ?",
                (code, code)
            ).fetchone()
            if not exists:
                return code


# ── ترحيل products.json ─────────────────────────────────────
def migrate_from_json(json_path: str, owner_id: int) -> None:
    if not os.path.exists(json_path):
        return
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    add_shop(owner_id)
    for code, p in data.get("products", {}).items():
        sizes = (
            p["sizes"] if isinstance(p["sizes"], list)
            else [s.strip() for s in p["sizes"].split(",")]
        )
        try:
            add_product(code, owner_id, p["name"], float(p["price"]), sizes)
        except Exception:
            pass
    with _conn() as con:
        for code in data.get("retired_codes", []):
            con.execute(
                "INSERT OR IGNORE INTO retired_codes (code) VALUES (?)", (code,)
            )
    os.rename(json_path, json_path + ".migrated")
