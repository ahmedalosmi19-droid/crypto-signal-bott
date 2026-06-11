

import os
import json
import sqlite3
import random
import string
from contextlib import contextmanager
from typing import Optional
 
DB_FILE = "bot.db"
 
 
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
        """)
 
        admin_id = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()
        if admin_id:
            con.execute(
                "INSERT OR IGNORE INTO admin (telegram_id) VALUES (?)",
                (int(admin_id),)
            )
 
 
def add_shop(telegram_id: int, username: Optional[str] = None) -> None:
    """أضف محلاً أو تجاهل إن كان موجوداً"""
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO shops (telegram_id, username) VALUES (?, ?)",
            (telegram_id, username)
        )
 
 
def get_shop(telegram_id: int) -> Optional[dict]:
    """اجلب بيانات محل بمعرّفه"""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM shops WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return dict(row) if row else None
 
 
def increment_message_count(shop_id: int) -> None:
    """زد عدّاد رسائل الزبائن"""
    with _conn() as con:
        con.execute(
            "UPDATE shops SET message_count = message_count + 1 WHERE telegram_id = ?",
            (shop_id,)
        )
 
 
def add_product(code: str, shop_id: int, name: str, price: float, sizes: list) -> None:
    """أضف سلعة جديدة"""
    with _conn() as con:
        con.execute(
            "INSERT INTO products (code, shop_id, name, price, sizes) VALUES (?, ?, ?, ?, ?)",
            (code, shop_id, name, price, ",".join(sizes))
        )
 
 
def get_product(code: str) -> Optional[dict]:
    """اجلب سلعة بكودها"""
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
    """اجلب كل سلع محل معيّن"""
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
 
 
def delete_product(code: str) -> bool:
    """احذف السلعة وانقل كودها لقائمة المتقاعدة"""
    with _conn() as con:
        affected = con.execute(
            "DELETE FROM products WHERE code = ?", (code,)
        ).rowcount
        if affected:
            con.execute(
                "INSERT OR IGNORE INTO retired_codes (code) VALUES (?)", (code,)
            )
        return bool(affected)
 
 
def generate_unique_code() -> str:
    """ولّد كوداً فريداً عبر كل المنصّة"""
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
 
 
def migrate_from_json(json_path: str, owner_id: int) -> None:
    """رحّل products.json إلى DB إن وُجد الملف، ثم أعد تسميته"""
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
