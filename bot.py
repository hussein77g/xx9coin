import os
import json
import sqlite3
import asyncio
import hashlib
import hmac
import threading
from datetime import datetime
from urllib.parse import parse_qsl
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler,
    filters, CallbackQueryHandler
)

# ═══════════════════════════════════════
BOT_TOKEN = "6915663970:AAEEqUWgSjLn-OhmPybL-OJ6qkoIOSl_wl8"
WEBAPP_URL = "https://xx9coin.vercel.app"
DB_PATH = "xx9.db"
CHANNEL_ID = -1004448656917
OWNER_ID = 6432606301
API_PORT = 8080

# ═══════════════════════════════════════
#   قاعدة البيانات
# ═══════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            referrer_id INTEGER,
            referrals INTEGER DEFAULT 0,
            wallet TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def upsert_user(user_id, username, first_name, balance, wallet, referrer_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute(
            "UPDATE users SET balance = ?, wallet = ?, username = ?, first_name = ?, updated_at = ? WHERE user_id = ?",
            (balance, wallet, username, first_name, datetime.now().isoformat(), user_id)
        )
    else:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, balance, wallet, referrer_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, balance, wallet, referrer_id, datetime.now().isoformat())
        )
    conn.commit()
    conn.close()

def add_balance(user_id, amount):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
              (amount, datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def add_referral(referrer_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (referrer_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, balance, referrals, wallet FROM users")
    rows = c.fetchall()
    conn.close()
    return rows

# ═══════════════════════════════════════
#   Verification
# ═══════════════════════════════════════
def verify_telegram(init_data):
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed:
            return None
        received_hash = parsed.pop("hash")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if computed != received_hash:
            return None
        return json.loads(parsed.get("user", "{}"))
    except:
        return None

# ═══════════════════════════════════════
#   API Handler
# ═══════════════════════════════════════
class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, code, obj):
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == '/':
            self._json(200, {"status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            data = json.loads(body) if body else {}

            # ─── /sync ───
            if self.path == '/sync':
                user = verify_telegram(data.get('initData', ''))
                if not user:
                    self._json(401, {"error": "invalid"})
                    return
                uid = user.get('id')
                upsert_user(uid, user.get('username', ''), user.get('first_name', ''),
                            data.get('balance', 0), data.get('wallet', ''))
                self._json(200, {"ok": True})

            # ─── /me ───
            elif self.path == '/me':
                user = verify_telegram(data.get('initData', ''))
                if not user:
                    self._json(401, {"error": "invalid"})
                    return
                uid = user.get('id')
                row = get_user(uid)
                if row:
                    self._json(200, {"user_id": row[0], "balance": row[3], "referrals": row[5] or 0, "wallet": row[6] or ""})
                else:
                    self._json(200, {"user_id": uid, "balance": 0, "referrals": 0, "wallet": ""})

            # ─── /leaderboard ───
            elif self.path == '/leaderboard':
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT user_id, username, first_name, balance FROM users ORDER BY balance DESC LIMIT 100")
                rows = c.fetchall()
                conn.close()
                users = []
                for r in rows:
                    users.append({
                        "user_id": r[0],
                        "username": r[1] or "",
                        "first_name": r[2] or "Player",
                        "balance": r[3] or 0
                    })
                self._json(200, {"users": users})

            # ─── /admin/stats ───
            elif self.path == '/admin/stats':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM users")
                users_count = c.fetchone()[0]
                c.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
                total = c.fetchone()[0]
                c.execute("SELECT COALESCE(SUM(referrals), 0) FROM users")
                refs = c.fetchone()[0]
                conn.close()
                self._json(200, {"users": users_count, "total_balance": total, "referrals": refs})

            # ─── /admin/give ───
            elif self.path == '/admin/give':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                target = int(data.get('user_id', 0))
                amount = float(data.get('amount', 0))
                if not get_user(target):
                    self._json(200, {"ok": False, "error": "User not found"})
                    return
                add_balance(target, amount)
                self._json(200, {"ok": True})

            # ─── /admin/broadcast ───
            elif self.path == '/admin/broadcast':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                msg = data.get('message', '').strip()
                if msg:
                    with open('broadcast_queue.txt', 'w', encoding='utf-8') as f:
                        f.write(msg)
                self._json(200, {"ok": True, "queued": True})

            # ─── /admin/user ───
            elif self.path == '/admin/user':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                target = int(data.get('user_id', 0))
                row = get_user(target)
                if not row:
                    self._json(200, {"error": "Not found"})
                    return
                self._json(200, {"user": {
                    "user_id": row[0], "username": row[1] or "", "first_name": row[2] or "",
                    "balance": row[3] or 0, "referrals": row[5] or 0, "wallet": row[6] or ""
                }})

            else:
                self.send_response(404)
                self._cors()
                self.end_headers()
        except Exception as e:
            print(f"API error: {e}")
            try:
                self.send_response(500)
                self._cors()
                self.end_headers()
            except:
                pass

def start_api():
    server = HTTPServer(('0.0.0.0', API_PORT), APIHandler)
    print(f"🌐 API يعمل على المنفذ {API_PORT}")
    server.serve_forever()

# ═══════════════════════════════════════
#   Telegram Bot
# ═══════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    un = user.username or ""
    fn = user.first_name or ""

    referrer_id = None
    if context.args:
        try:
            ref = int(context.args[0])
            if ref != uid:
                referrer_id = ref
        except:
            pass

    existing = get_user(uid)
    if not existing:
        upsert_user(uid, un, fn, 0, "", referrer_id)
        if referrer_id and get_user(referrer_id):
            add_balance(referrer_id, 5.0)
            add_referral(referrer_id)
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 صديق جديد انضم!\n💰 +5 xx9\n👤 {fn}"
                )
            except:
                pass

    row = get_user(uid)
    balance = row[3] if row else 0
    refs = row[5] if row else 0

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 العب الآن", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton("👥 دعوة أصدقاء", callback_data="invite")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")]
    ])

    await update.message.reply_text(
        f"🎮 أهلاً {fn}!\n\n💰 رصيدك: {balance:.2f} xx9\n👥 أصدقاؤك: {refs}",
        reply_markup=kb
    )

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("استخدم /start أولاً")
        return
    await update.message.reply_text(f"💰 رصيدك: {row[3]:.2f} xx9\n👥 أصدقاؤك: {row[5] or 0}")

async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    link = f"https://t.me/xx9coin_bot?start={uid}"
    row = get_user(uid)
    refs = (row[5] or 0) if row else 0
    await update.message.reply_text(
        f"👥 نظام الإحالة\n\n🎁 +5 xx9 لكل صديق\n\n🔗 رابطك:\n{link}\n\n👤 أصدقاؤك: {refs}"
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    users = get_all_users()
    total = sum(u[3] for u in users)
    await update.message.reply_text(
        f"📊 إحصائيات\n\n👥 المستخدمون: {len(users)}\n💰 إجمالي: {total:.2f} xx9"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "invite":
        link = f"https://t.me/xx9coin_bot?start={uid}"
        row = get_user(uid)
        refs = (row[5] or 0) if row else 0
        await q.message.reply_text(f"👥 دعوة أصدقاء\n\n🔗 {link}\n\n👤 أصدقاؤك: {refs}")
    elif q.data == "stats":
        row = get_user(uid)
        if row:
            await q.message.reply_text(f"💰 {row[3]:.2f} xx9\n👥 {row[5] or 0} أصدقاء")

async def post_init(app):
    init_db()
    print("✅ قاعدة البيانات جاهزة")

def main():
    print("🚀 جاري التشغيل...")

    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()

    app = (Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ البوت يعمل")
    print("💡 CTRL+C للإيقاف")
    app.run_polling()

if __name__ == "__main__":
    main()
