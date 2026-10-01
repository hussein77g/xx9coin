import os
import json
import sqlite3
import asyncio
import hashlib
import hmac
import threading
from datetime import datetime, timedelta, timezone
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
API_PORT = int(os.environ.get("PORT", 8080))

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
    c.execute("""
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            image_url TEXT,
            link_url TEXT,
            sponsor TEXT,
            price REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            days INTEGER DEFAULT 7,
            expires_at TEXT,
            clicks INTEGER DEFAULT 0,
            created_at TEXT
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
            print("VERIFY: no hash in initData")
            return None
        received_hash = parsed.pop("hash")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        
        # CORRECT Telegram verification
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        
        if computed != received_hash:
            print(f"VERIFY: hash mismatch")
            return None
        return json.loads(parsed.get("user", "{}"))
    except Exception as e:
        print(f"VERIFY error: {e}")
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
        if self.path == '/' or self.path == '':
            self._json(200, {"status": "ok"})
        elif self.path == '/ads':
            try:
                from datetime import datetime as dt
                conn = sqlite3.connect(DB_PATH)
                c2 = conn.cursor()
                now = dt.now().isoformat()
                c2.execute("SELECT id, title, description, image_url, link_url FROM ads WHERE active = 1 AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC LIMIT 20", (now,))
                rows = c2.fetchall()
                conn.close()
                ads = []
                for r in rows:
                    ads.append({
                        "id": r[0], "title": r[1], "desc": r[2] or "",
                        "img": r[3] or "", "url": r[4] or ""
                    })
                self._json(200, {"ads": ads})
            except Exception as e:
                print(f"Ads GET error: {e}")
                self._json(200, {"ads": []})
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
                client_balance = float(data.get('balance', 0))
                wallet = data.get('wallet', '')
                current = get_user(uid)
                if current:
                    # Take the MAX - if server has more (admin gave), keep it
                    # If client has more (user tapped), take it
                    final_balance = max(current[3] or 0, client_balance)
                else:
                    final_balance = client_balance
                upsert_user(uid, user.get('username', ''), user.get('first_name', ''),
                            final_balance, wallet)
                self._json(200, {"ok": True, "balance": final_balance})

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

            # ─── /admin/ads/create ───
            elif self.path == '/admin/ads/create':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                try:
                    title = data.get('title', '').strip()
                    desc = data.get('desc', '').strip()
                    img = data.get('img', '').strip()
                    url = data.get('url', '').strip()
                    sponsor = data.get('sponsor', '').strip()
                    price = float(data.get('price', 0))
                    days = int(data.get('days', 7))
                    if not title or not url or days < 1:
                        self._json(400, {"error": "بيانات ناقصة"})
                        return
                    from datetime import datetime as dt, timedelta
                    now = dt.now()
                    expires = (now + timedelta(days=days)).isoformat()
                    conn = sqlite3.connect(DB_PATH)
                    c2 = conn.cursor()
                    c2.execute("INSERT INTO ads (title, description, image_url, link_url, sponsor, price, active, days, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                        (title, desc, img, url, sponsor, price, days, expires, now.isoformat()))
                    ad_id = c2.lastrowid
                    conn.commit()
                    conn.close()
                    self._json(200, {"ok": True, "ad_id": ad_id, "days": days})
                except Exception as e:
                    self._json(500, {"error": str(e)})

            # ─── /admin/ads/list ───
            elif self.path == '/admin/ads/list':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                try:
                    conn = sqlite3.connect(DB_PATH)
                    c2 = conn.cursor()
                    c2.execute("SELECT id, title, description, image_url, link_url, sponsor, price, active, days, expires_at, clicks, created_at FROM ads ORDER BY id DESC LIMIT 50")
                    rows = c2.fetchall()
                    conn.close()
                    ads = []
                    for r in rows:
                        ads.append({
                            "id": r[0], "title": r[1], "desc": r[2] or "", "img": r[3] or "",
                            "url": r[4] or "", "sponsor": r[5] or "", "price": r[6] or 0,
                            "active": r[7], "days": r[8], "expires_at": r[9],
                            "clicks": r[10] or 0, "created_at": r[11]
                        })
                    self._json(200, {"ads": ads})
                except Exception as e:
                    self._json(500, {"error": str(e)})

            # ─── /admin/ads/delete ───
            elif self.path == '/admin/ads/delete':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                try:
                    ad_id = int(data.get('ad_id', 0))
                    conn = sqlite3.connect(DB_PATH)
                    c2 = conn.cursor()
                    c2.execute("DELETE FROM ads WHERE id = ?", (ad_id,))
                    conn.commit()
                    conn.close()
                    self._json(200, {"ok": True})
                except Exception as e:
                    self._json(500, {"error": str(e)})

            # ─── /admin/ads/toggle ───
            elif self.path == '/admin/ads/toggle':
                user = verify_telegram(data.get('initData', ''))
                if not user or user.get('id') != OWNER_ID:
                    self._json(403, {"error": "forbidden"})
                    return
                try:
                    ad_id = int(data.get('ad_id', 0))
                    conn = sqlite3.connect(DB_PATH)
                    c2 = conn.cursor()
                    c2.execute("UPDATE ads SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?", (ad_id,))
                    conn.commit()
                    conn.close()
                    self._json(200, {"ok": True})
                except Exception as e:
                    self._json(500, {"error": str(e)})

            # ─── /ad/click ───
            elif self.path == '/ad/click':
                try:
                    ad_id = int(data.get('ad_id', 0))
                    conn = sqlite3.connect(DB_PATH)
                    c2 = conn.cursor()
                    c2.execute("UPDATE ads SET clicks = clicks + 1 WHERE id = ?", (ad_id,))
                    conn.commit()
                    conn.close()
                except:
                    pass
                self._json(200, {"ok": True})

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
                    text=f"صديق جديد انضم من رابطك!\n+5 xx9\n{fn}"
                )
            except:
                pass

    buttons = [[InlineKeyboardButton("🚀 افتح البوت", web_app=WebAppInfo(url=WEBAPP_URL))]]

    # Add admin button for owner only
    if uid == OWNER_ID:
        buttons.append([InlineKeyboardButton("👑 لوحة التحكم", callback_data="admin_panel")])

    kb = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        f"اهلا {fn}!",
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



async def auto_delete_expired_ads(app):
    """حذف تلقائي للإعلانات المنتهية - كل 60 ثانية"""
    while True:
        try:
            await asyncio.sleep(60)
            
            # Baghdad timezone
            BAGHDAD_TZ = timezone(timedelta(hours=3))
            now = datetime.now(BAGHDAD_TZ)
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Get expired ads first (for logging)
            c.execute("SELECT id, title, expires_at FROM ads WHERE expires_at IS NOT NULL AND expires_at < ?", (now.isoformat(),))
            expired = c.fetchall()
            
            if expired:
                for ad in expired:
                    print(f"🗑 حذف إعلان منتهي #{ad[0]}: {ad[1]}")
                
                # Delete them
                c.execute("DELETE FROM ads WHERE expires_at IS NOT NULL AND expires_at < ?", (now.isoformat(),))
                deleted = c.rowcount
                conn.commit()
                print(f"✅ تم حذف {deleted} إعلان منتهي")
            
            conn.close()
        except Exception as e:
            print(f"Auto-delete error: {e}")

async def post_init(app):
    init_db()
    print("✅ قاعدة البيانات جاهزة")
    asyncio.create_task(auto_delete_expired_ads(app))
    print("🗑 حذف تلقائي للإعلانات المنتهية كل دقيقة")



# ═══════════════════════════════════════
#   ADMIN PANEL IN BOT
# ═══════════════════════════════════════


def format_baghdad_time(dt):
    """Format time in Baghdad style: 12 صباحاً / 3 مساءً"""
    hour = dt.hour % 12
    if hour == 0:
        hour = 12
    minute = dt.minute
    period = "صباحاً" if dt.hour < 12 else "مساءً"
    day = dt.day
    month = dt.month
    year = dt.year
    return f"{hour}:{minute:02d} {period} — {day}/{month}/{year}"

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 إعطاء عملات", callback_data="adm_give")],
        [InlineKeyboardButton("📢 نشر إعلان", callback_data="adm_ad_create")],
        [InlineKeyboardButton("📋 الإعلانات الحالية", callback_data="adm_ad_list")],
        [InlineKeyboardButton("📨 رسالة جماعية", callback_data="adm_broadcast")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="adm_stats")],
        [InlineKeyboardButton("🔍 بحث مستخدم", callback_data="adm_search")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="adm_back")],
    ])


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if uid != OWNER_ID:
        await q.answer("للمالك فقط", show_alert=True)
        return

    data = q.data

    if data == "admin_panel":
        await q.message.edit_text("👑 لوحة المالك\n\nاختر العملية:", reply_markup=admin_menu_kb())

    elif data == "adm_back":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 افتح البوت", web_app=WebAppInfo(url=WEBAPP_URL))],
            [InlineKeyboardButton("👑 لوحة المالك", callback_data="admin_panel")],
        ])
        await q.message.edit_text(f"اهلا {q.from_user.first_name}!", reply_markup=kb)

    elif data == "adm_stats":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        users = c.fetchone()[0]
        c.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COALESCE(SUM(referrals), 0) FROM users")
        refs = c.fetchone()[0]
        conn.close()
        BAGHDAD_TZ = timezone(timedelta(hours=3))
        now = datetime.now(BAGHDAD_TZ)
        day_ago = now - timedelta(hours=24)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE updated_at > ?", (day_ago.isoformat(),))
        active = c.fetchone()[0]
        c.execute("SELECT COALESCE(SUM(referrals), 0) FROM users")
        refs = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM ads WHERE active = 1 AND (expires_at IS NULL OR expires_at > ?)", (now.isoformat(),))
        ads = c.fetchone()[0]
        conn.close()

        # Timestamp for refresh
        hour = now.hour % 12
        if hour == 0: hour = 12
        period = "ص" if now.hour < 12 else "م"
        time_str = f"{hour}:{now.minute:02d}:{now.second:02d} {period}"

        text = (
            f"📊 إحصائيات البوت\n\n"
            f"👥 المستخدمون: {users:,}\n"
            f"🟢 النشطون (24 ساعة): {active:,}\n"
            f"🔗 إجمالي الإحالات: {refs:,}\n"
            f"📢 الإعلانات النشطة: {ads}\n\n"
            f"🕒 آخر تحديث: {time_str}"
        )
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تحديث", callback_data="adm_stats")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
        ]))

    elif data == "adm_give":
        await q.message.edit_text(
            "💰 إعطاء عملات\n\nأرسل:\n`USER_ID AMOUNT`\n\nمثال:\n`6432606301 100.00`\n\n❌ /cancel للإلغاء",
            parse_mode="Markdown"
        )
        context.user_data["admin_action"] = "give"

    elif data == "adm_broadcast":
        await q.message.edit_text("📨 رسالة جماعية\n\nأرسل النص:\n\n❌ /cancel للإلغاء")
        context.user_data["admin_action"] = "broadcast"

    elif data == "adm_search":
        await q.message.edit_text("🔍 بحث مستخدم\n\nأرسل USER_ID:\n\n❌ /cancel للإلغاء")
        context.user_data["admin_action"] = "search"

    elif data == "adm_ad_create":
        await q.message.edit_text(
            "📢 نشر إعلان جديد\n\n"
            "🟢 الخطوة 1 من 4\n\n"
            "أرسل اسم القناة / المشروع:\n\n"
            "❌ /cancel للإلغاء"
        )
        context.user_data["admin_action"] = "ad_channel"

    elif data == "adm_ad_list":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, title, link_url, active, days, expires_at, clicks FROM ads ORDER BY id DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()

        if not rows:
            await q.message.edit_text(
                "📋 لا يوجد إعلانات حالياً",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
                ])
            )
            return

        # Build message
        BAGHDAD_TZ = timezone(timedelta(hours=3))
        now = datetime.now(BAGHDAD_TZ)

        text = "📋 الإعلانات الحالية\n\n"
        buttons = []
        for r in rows:
            ad_id = r[0]
            title = r[1]
            url = r[2]
            active = r[3]
            days = r[4]
            expires_str = r[5]
            clicks = r[6]

            # Status
            try:
                expires = datetime.fromisoformat(expires_str).astimezone(BAGHDAD_TZ) if expires_str else None
            except:
                expires = None

            is_expired = expires and expires < now

            if not active:
                status = "⏸ موقوف"
            elif is_expired:
                status = "⌛ منتهي"
            else:
                status = "✅ نشط"

            # Remaining time
            remaining = ""
            if expires and not is_expired and active:
                delta = expires - now
                total_hours = int(delta.total_seconds() / 3600)
                if total_hours < 24:
                    remaining = f" (باقي {total_hours} ساعة)"
                else:
                    remaining = f" (باقي {int(delta.days)} يوم)"

            text += f"#{ad_id} — {title}\n"
            text += f"{status}{remaining}\n"
            text += f"👁 {clicks} نقرة | 📅 {days} أيام\n\n"

            # Add buttons row for this ad
            buttons.append([
                InlineKeyboardButton(f"🗑 حذف #{ad_id}", callback_data=f"adm_ad_del_{ad_id}"),
                InlineKeyboardButton(f"⏸ {'تفعيل' if not active else 'إيقاف'} #{ad_id}", callback_data=f"adm_ad_tog_{ad_id}"),
            ])

        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])

        await q.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True
        )

    # ─── Delete Ad ───
    elif data.startswith("adm_ad_del_"):
        try:
            ad_id = int(data.replace("adm_ad_del_", ""))
        except:
            await q.answer("خطأ في المعرّف", show_alert=True)
            return

        # Get ad title for confirmation
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT title FROM ads WHERE id = ?", (ad_id,))
        row = c.fetchone()

        if not row:
            conn.close()
            await q.answer("الإعلان غير موجود", show_alert=True)
            return

        ad_title = row[0]
        c.execute("DELETE FROM ads WHERE id = ?", (ad_id,))
        conn.commit()
        conn.close()

        await q.answer(f"✅ تم حذف: {ad_title}", show_alert=True)

        # Reload list
        # (recursive call removed)

    # ─── Toggle Ad Active ───
    elif data.startswith("adm_ad_tog_"):
        try:
            ad_id = int(data.replace("adm_ad_tog_", ""))
        except:
            await q.answer("خطأ", show_alert=True)
            return

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE ads SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?", (ad_id,))
        conn.commit()

        c.execute("SELECT active FROM ads WHERE id = ?", (ad_id,))
        row = c.fetchone()
        conn.close()

        if row:
            status = "مفعّل ✅" if row[0] else "موقوف ⏸"
            await q.answer(f"تم التغيير: {status}", show_alert=True)

        # Reload list
        # (recursive call removed)


async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    action = context.user_data.get("admin_action")
    if not action:
        return

    text = update.message.text.strip()

    if action == "give":
        try:
            parts = text.split()
            if len(parts) != 2:
                raise ValueError("يجب إرسال قيمتين فقط")
            
            target = int(parts[0])
            amount = float(parts[1])
            
            if target < 100000:
                raise ValueError("USER_ID غير صحيح (يجب أن يكون رقماً كبيراً)")
            
            if amount <= 0:
                raise ValueError("المبلغ يجب أن يكون موجباً")
            
            if not get_user(target):
                await update.message.reply_text(
                    f"❌ المستخدم {target} غير موجود",
                    reply_markup=admin_menu_kb()
                )
                context.user_data.pop("admin_action", None)
                return
            
            add_balance(target, amount)
            new_bal = get_user(target)[3]
            await update.message.reply_text(
                f"✅ تم إعطاء {amount:.2f} xx9\n👤 للمستخدم: {target}\n💰 رصيده الآن: {new_bal:.2f}",
                reply_markup=admin_menu_kb()
            )
            context.user_data.pop("admin_action", None)
        except ValueError as e:
            await update.message.reply_text(
                f"❌ صيغة خاطئة\n\n`{e}`\n\nالصيغة الصحيحة:\n`USER_ID AMOUNT`\n\nمثال:\n`6432606301 100.00`",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {e}")

    elif action == "broadcast":
        users = get_all_users()
        await update.message.reply_text(f"📨 جاري الإرسال لـ {len(users)}...")
        success = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u[0], text=text)
                success += 1
            except:
                pass
        await update.message.reply_text(f"✅ نجح: {success}/{len(users)}", reply_markup=admin_menu_kb())
        context.user_data.pop("admin_action", None)

    elif action == "search":
        try:
            target = int(text)
            row = get_user(target)
            if not row:
                await update.message.reply_text("❌ غير موجود", reply_markup=admin_menu_kb())
                return
            info = f"👤 معلومات\n\n🆔 {row[0]}\n📛 {row[2] or '-'}\n👤 @{row[1] or 'none'}\n💰 {row[3]:.2f} xx9\n👥 {row[5] or 0}"
            await update.message.reply_text(info, reply_markup=admin_menu_kb())
            context.user_data.pop("admin_action", None)
        except:
            await update.message.reply_text("❌ أرسل ID صحيح")

    elif action == "ad_channel":
        context.user_data["ad_channel"] = text
        context.user_data["admin_action"] = "ad_desc"
        await update.message.reply_text(
            f"✅ اسم القناة: {text}\n\n"
            "🟢 الخطوة 2 من 4\n\n"
            "أرسل الوصف (نص قصير يظهر تحت الاسم):\n\n"
            "❌ /cancel للإلغاء"
        )

    elif action == "ad_desc":
        context.user_data["ad_desc"] = text
        context.user_data["admin_action"] = "ad_url"
        await update.message.reply_text(
            f"✅ الوصف: {text}\n\n"
            "🟢 الخطوة 3 من 4\n\n"
            "أرسل رابط القناة / المشروع:\n\n"
            "مثال: https://t.me/mychannel\n\n"
            "❌ /cancel للإلغاء"
        )

    elif action == "ad_url":
        # Validate URL
        if not (text.startswith("https://t.me/") or text.startswith("http://") or text.startswith("https://")):
            await update.message.reply_text(
                "❌ الرابط غير صحيح\n\n"
                "لازم يبدأ بـ https://t.me/ أو https://\n\n"
                "جرب مرة ثانية:"
            )
            return
        context.user_data["ad_url"] = text
        context.user_data["admin_action"] = "ad_days"
        await update.message.reply_text(
            f"✅ الرابط: {text}\n\n"
            "🟢 الخطوة 4 من 4\n\n"
            "كم مدة تثبيت الإعلان؟\n\n"
            "أرسل مثال:\n"
            "• `5 ساعات`\n"
            "• `1 يوم`\n"
            "• `7 أيام`\n"
            "• `40 يوم`\n\n"
            "❌ /cancel للإلغاء",
            parse_mode="Markdown"
        )

    elif action == "ad_days":
        # Parse time string
        import re as _re
        hours_match = _re.search(r'(\d+)\s*(ساعة|ساعات|ساعه|ساعتين|hour|hours)', text)
        days_match = _re.search(r'(\d+)\s*(يوم|أيام|ايام|يومان|day|days)', text)
        
        if hours_match:
            value = int(hours_match.group(1))
            total_days = value / 24.0
            duration_text = f"{value} ساعة"
        elif days_match:
            value = int(days_match.group(1))
            total_days = float(value)
            duration_text = f"{value} يوم"
        else:
            await update.message.reply_text(
                "❌ الصيغة غير صحيحة\n\n"
                "أرسل مثلاً:\n"
                "• `5 ساعات`\n"
                "• `1 يوم`\n"
                "• `7 أيام`\n\n"
                "جرب مرة ثانية:",
                parse_mode="Markdown"
            )
            return
        
        if total_days <= 0:
            await update.message.reply_text("❌ المدة يجب أن تكون أكبر من صفر")
            return
        
        # Baghdad timezone (UTC+3)
        BAGHDAD_TZ = timezone(timedelta(hours=3))
        now = datetime.now(BAGHDAD_TZ)
        expires = now + timedelta(days=total_days)
        
        # Get collected data
        title = context.user_data.get("ad_channel", "")
        desc = context.user_data.get("ad_desc", "")
        url = context.user_data.get("ad_url", "")
        
        # Save to DB
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO ads (title, description, image_url, link_url, sponsor, price, active, days, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (title, desc, '', url, '', 0, int(total_days) if total_days == int(total_days) else 1, expires.isoformat(), now.isoformat()))
        ad_id = c.lastrowid
        conn.commit()
        conn.close()
        
        # Format times for display
        now_str = format_baghdad_time(now)
        expires_str = format_baghdad_time(expires)
        
        await update.message.reply_text(
            f"✅ تم إضافة الإعلان #{ad_id}\n\n"
            f"📢 {title}\n"
            f"📝 {desc}\n"
            f"🔗 {url}\n\n"
            f"⏰ المدة: {duration_text}\n"
            f"📅 من: {now_str}\n"
            f"📅 إلى: {expires_str}",
            reply_markup=admin_menu_kb(),
            disable_web_page_preview=True
        )
        # Clear all ad data
        for k in ["ad_channel", "ad_desc", "ad_url", "admin_action"]:
            context.user_data.pop(k, None)


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("admin_action", None)
    await update.message.reply_text("❌ تم الإلغاء", reply_markup=admin_menu_kb())



def main():
    print("🚀 جاري التشغيل...")

    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()

    app = (Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build())

    from telegram.ext import MessageHandler, filters, CallbackQueryHandler

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("invite", cmd_invite))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("cancel", admin_cancel))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^adm_|^admin_panel$"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))

    print("✅ البوت يعمل")
    print("💡 CTRL+C للإيقاف")
    app.run_polling()

if __name__ == "__main__":
    main()
