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

async def post_init(app):
    init_db()
    print("✅ قاعدة البيانات جاهزة")



# ═══════════════════════════════════════
#   ADMIN PANEL IN BOT
# ═══════════════════════════════════════
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
        text = f"📊 إحصائيات البوت\n\n👥 المستخدمون: {users}\n💰 إجمالي العملات: {total:.2f} xx9\n🔗 الإحالات: {refs}"
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
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
            "📢 نشر إعلان\n\nأرسل:\n`TITLE | DESC | URL | DAYS`\n\nمثال:\n`قناتي | انضم | https://t.me/mychannel | 7`\n\n❌ /cancel للإلغاء",
            parse_mode="Markdown"
        )
        context.user_data["admin_action"] = "ad_create"

    elif data == "adm_ad_list":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, title, link_url, active, days, expires_at, clicks FROM ads ORDER BY id DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()
        if not rows:
            text = "📋 لا يوجد إعلانات"
        else:
            text = "📋 الإعلانات:\n\n"
            for r in rows:
                status = "✅ نشط" if r[3] else "⏸ موقوف"
                text += f"#{r[0]} {r[1]}\n{status} | {r[6]} نقرة\n🔗 {r[2]}\n\n"
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
        ]), disable_web_page_preview=True)


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

    elif action == "ad_create":
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 4:
                raise ValueError("محتاج 4")
            title = parts[0]
            desc = parts[1]
            url = parts[2]
            days = int(parts[3])

            from datetime import timedelta
            now = datetime.now()
            expires = (now + timedelta(days=days)).isoformat()

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO ads (title, description, image_url, link_url, sponsor, price, active, days, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (title, desc, '', url, '', 0, days, expires, now.isoformat()))
            ad_id = c.lastrowid
            conn.commit()
            conn.close()
            await update.message.reply_text(
                f"✅ تم إضافة الإعلان #{ad_id}\n📢 {title}\n🔗 {url}\n📅 {days} أيام",
                reply_markup=admin_menu_kb(),
                disable_web_page_preview=True
            )
            context.user_data.pop("admin_action", None)
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {e}\n\nمثال:\n`قناتي | انضم | https://t.me/mychannel | 7`", parse_mode="Markdown")


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
