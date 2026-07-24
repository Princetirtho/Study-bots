import logging
import asyncio
import aiosqlite
import os
import re
import html
import threading
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import pytz
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, 
    KeyboardButton,
    ChatMember
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ContextTypes,
    filters
)
from telegram.error import TelegramError
from telegram.request import HTTPXRequest

# ==================== ডামি ওয়েব সার্ভার ( Render/UptimeRobot এর জন্য ) ====================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running successfully!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 Web Server started on port {port} for UptimeRobot pings.")
    server.serve_forever()

# ==================== কনফিগারেশন ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID =8212595643 #8659434858

REQUIRED_GROUPS = [
    "https://t.me/STUDY_ROOM_OFFICIAL",
    "https://t.me/STUDY_ROOM_PAID",
    "https://t.me/STUDY_ROOM_FREE"
]

TEST_MODE = True  
BD_TZ = pytz.timezone('Asia/Dhaka')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_NAME = "study_room.db"
BACKUP_DIR = "database_backups"

# ==================== ডেটাবেজ ব্যাকআপ সিস্টেম ====================
def ensure_backup_dir():
    """ব্যাকআপ ডিরেক্টরি তৈরি করে"""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        print(f"📁 Backup directory created: {BACKUP_DIR}")

async def create_database_backup():
    """ডেটাবেজের ব্যাকআপ তৈরি করে"""
    try:
        ensure_backup_dir()
        
        if not os.path.exists(DB_NAME):
            print("❌ Database not found! No backup created.")
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"study_room_backup_{timestamp}.db")
        
        shutil.copy2(DB_NAME, backup_file)
        print(f"✅ Database backup created: {backup_file}")
        
        # সর্বশেষ ৫টি ব্যাকআপ রাখুন
        keep_latest_backups(5)
        
        return backup_file
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return None

def keep_latest_backups(keep_count=5):
    """সর্বশেষ কয়টি ব্যাকআপ রাখবে"""
    try:
        if not os.path.exists(BACKUP_DIR):
            return
        
        backups = []
        for f in os.listdir(BACKUP_DIR):
            if f.startswith("study_room_backup_") and f.endswith(".db"):
                file_path = os.path.join(BACKUP_DIR, f)
                backups.append((os.path.getctime(file_path), file_path))
        
        backups.sort(reverse=True)
        
        for _, file_path in backups[keep_count:]:
            os.remove(file_path)
            print(f"🗑️ Old backup deleted: {os.path.basename(file_path)}")
            
    except Exception as e:
        print(f"⚠️ Error cleaning backups: {e}")

async def list_all_backups():
    """সব ব্যাকআপের লিস্ট দেখায়"""
    try:
        if not os.path.exists(BACKUP_DIR):
            return []
        
        backups = []
        for f in os.listdir(BACKUP_DIR):
            if f.startswith("study_room_backup_") and f.endswith(".db"):
                file_path = os.path.join(BACKUP_DIR, f)
                size = os.path.getsize(file_path) / 1024
                created = datetime.fromtimestamp(os.path.getctime(file_path))
                backups.append({
                    'filename': f,
                    'path': file_path,
                    'size': round(size, 2),
                    'created': created
                })
        
        backups.sort(key=lambda x: x['created'], reverse=True)
        return backups
    except Exception as e:
        print(f"❌ Error listing backups: {e}")
        return []

async def restore_database_from_backup(backup_filename):
    """ব্যাকআপ থেকে ডেটাবেজ রিস্টোর করে"""
    try:
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        if not os.path.exists(backup_path):
            print(f"❌ Backup file not found: {backup_filename}")
            return False
        
        # রিস্টোরের আগে বর্তমান ডেটাবেজের ব্যাকআপ নিন
        await create_database_backup()
        
        shutil.copy2(backup_path, DB_NAME)
        print(f"✅ Database restored from: {backup_filename}")
        return True
    except Exception as e:
        print(f"❌ Restore failed: {e}")
        return False

# ==================== ডেটাবেজ সেটআপ ====================
async def init_db(application: Application):
    """ডেটাবেজ ইনিশিয়ালাইজ করে"""
    
    # ব্যাকআপ নিন (যদি ডেটাবেজ থাকে)
    if os.path.exists(DB_NAME):
        await create_database_backup()
        print("📦 Database backup created before initialization")
    
    # ডেটাবেজ চেক করুন
    if os.path.exists(DB_NAME):
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("SELECT platform FROM categories LIMIT 1")
                print("✅ Existing database is valid")
        except Exception as e:
            print(f"⚠️ Database corrupted: {e}")
            
            # ব্যাকআপ থেকে রিস্টোর করার চেষ্টা
            backups = await list_all_backups()
            if backups:
                latest_backup = backups[0]['filename']
                print(f"🔄 Attempting to restore from latest backup: {latest_backup}")
                
                if await restore_database_from_backup(latest_backup):
                    print("✅ Database restored successfully!")
                    try:
                        async with aiosqlite.connect(DB_NAME) as db:
                            await db.execute("SELECT platform FROM categories LIMIT 1")
                            return
                    except:
                        print("⚠️ Restored database also corrupted, creating fresh...")
            
            # ব্যাকআপ না থাকলে ডিলিট
            if os.path.exists(DB_NAME):
                os.remove(DB_NAME)
                print("🗑️ Corrupted database deleted")
    
    # নতুন ডেটাবেজ তৈরি
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                points REAL DEFAULT 5.0,
                referred_by INTEGER,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referee_id INTEGER,
                status TEXT DEFAULT 'active',
                reward_given INTEGER DEFAULT 0,
                PRIMARY KEY (referrer_id, referee_id)
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                category_id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                subject TEXT,
                batch TEXT,
                is_active INTEGER DEFAULT 1
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS courses (
                course_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_key TEXT UNIQUE,
                course_name TEXT,
                category_id INTEGER,
                cycle TEXT,
                image_id TEXT,
                info_text TEXT,
                channel_id TEXT,
                points_required REAL DEFAULT 5.0,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (category_id) REFERENCES categories(category_id)
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_access (
                user_id INTEGER,
                course_id INTEGER,
                invite_link TEXT,
                used INTEGER DEFAULT 0,
                purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                join_date TIMESTAMP,
                PRIMARY KEY (user_id, course_id)
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS join_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                course_id INTEGER,
                join_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sub_admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_by INTEGER,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS required_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_type TEXT,
                group_id TEXT,
                is_active INTEGER DEFAULT 1
            )''')
        
        for group_url in REQUIRED_GROUPS:
            group_username = group_url.replace("https://t.me/", "")
            if not group_username.startswith("@"):
                group_username = "@" + group_username
                
            async with db.execute("SELECT 1 FROM required_groups WHERE group_id = ?", (group_username,)) as cursor:
                exists = await cursor.fetchone()
                if not exists:
                    await db.execute(
                        "INSERT INTO required_groups (group_type, group_id, is_active) VALUES (?, ?, ?)",
                        ("channel", group_username, 1)
                    )
        
        await db.commit()
        print("✅ New database created successfully!")
    
    # নতুন ডেটাবেজের ব্যাকআপ নিন
    await create_database_backup()
    print("📦 Initial backup created")

# ==================== হেল্পার ফাংশন ====================
def get_bd_time_str():
    return datetime.now(BD_TZ).strftime("%Y-%m-%d %I:%M %p")

async def is_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM sub_admins WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def is_super_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def is_user_joined(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT group_id FROM required_groups WHERE is_active = 1") as cursor:
                groups = await cursor.fetchall()
        
        for (group_id,) in groups:
            try:
                clean_group_id = group_id.replace("@", "")
                if not clean_group_id.startswith("@"):
                    clean_group_id = "@" + clean_group_id
                    
                member = await context.bot.get_chat_member(chat_id=clean_group_id, user_id=user_id)
                if member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                    return False
            except TelegramError:
                return False
        
        return True
    except Exception as e:
        logger.error(f"Join check error: {e}")
        return False

async def send_join_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT group_id FROM required_groups WHERE is_active = 1") as cursor:
            groups = await cursor.fetchall()
    
    keyboard = []
    for (group_id,) in groups:
        clean_id = group_id.replace("@", "")
        keyboard.append([InlineKeyboardButton(
            f"📢 {clean_id} তে জয়েন", 
            url=f"https://t.me/{clean_id}"
        )])
    
    keyboard.append([InlineKeyboardButton("✅ জয়েন করেছি", callback_data="check_join")])
    
    text = "🔒 <b>ভেরিফিকেশন প্রয়োজন!</b>\n\nবট ব্যবহার করতে আপনাকে আমাদের সব গ্রুপ ও চ্যানেলে জয়েন করতে হবে।"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def get_categories():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT category_id, platform, subject, batch FROM categories WHERE is_active = 1 ORDER BY platform, subject, batch") as cursor:
            return await cursor.fetchall()

async def get_category_courses(category_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT course_id, cycle, points_required FROM courses WHERE category_id = ? AND is_active = 1 ORDER BY cycle", (category_id,)) as cursor:
            return await cursor.fetchall()

# ==================== কিবোর্ড ====================
async def get_main_keyboard(user_id=None):
    keyboard = [
        [KeyboardButton("👤 প্রোফাইল"), KeyboardButton("📚 আমার কোর্স")],
        [KeyboardButton("🛒 কোর্স কিনুন"), KeyboardButton("🔗 রেফারেল লিংক")],
        [KeyboardButton("🏆 লিডারবোর্ড"), KeyboardButton("📂 সকল কোর্সসমূহ")],
        [KeyboardButton("💬 সাপোর্ট")]
    ]
    if user_id and await is_admin(user_id):
        keyboard.append([KeyboardButton("👑 অ্যাডমিন প্যানেল")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_main_keyboard():
    keyboard = [
        [KeyboardButton("📚 কোর্স ম্যানেজমেন্ট"), KeyboardButton("📁 ক্যাটাগরি ম্যানেজমেন্ট")],
        [KeyboardButton("👥 ইউজার ম্যানেজমেন্ট"), KeyboardButton("👑 সাব অ্যাডমিন")],
        [KeyboardButton("📢 ব্রডকাস্ট"), KeyboardButton("📦 ব্যাকআপ ম্যানেজমেন্ট")],
        [KeyboardButton("🔙 ইউজার মোড")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_backup_keyboard():
    return ReplyKeyboardMarkup([
        ["📦 ব্যাকআপ নাও", "📋 ব্যাকআপ লিস্ট"],
        ["🔄 ব্যাকআপ রিস্টোর"],
        ["🔙 ব্যাক", "🏠 মেইন মেনু"]
    ], resize_keyboard=True)

def get_admin_courses_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ সাইকেল যোগ", "📦 একসাথে সাইকেল যোগ"],
        ["📦 একসাথে ইনফো যোগ", "📦 একসাথে ছবি যোগ"],
        ["🗑 সাইকেল ডিলিট", "📋 সকল সাইকেল"],
        ["🔙 ব্যাক", "🏠 মেইন মেনু"]
    ], resize_keyboard=True)

def get_admin_categories_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ ক্যাটাগরি যোগ", "📦 একসাথে ক্যাটাগরি যোগ"],
        ["📋 সকল ক্যাটাগরি"],
        ["🔙 ব্যাক", "🏠 মেইন মেনু"]
    ], resize_keyboard=True)

def get_admin_users_keyboard():
    return ReplyKeyboardMarkup([
        ["👥 সকল ইউজার", "🔎 ইউজার ডিটেইলস"],
        ["💰 point add / remove", "📊 পারচেজ হিস্টোরি"],
        ["🔙 ব্যাক", "🏠 মেইন মেনু"]
    ], resize_keyboard=True)

def get_sub_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ সাব অ্যাডমিন যোগ"],
        ["📋 সাব অ্যাডমিন লিস্ট"],
        ["🔙 ব্যাক", "🏠 মেইন মেনু"]
    ], resize_keyboard=True)

def get_sub_admin_super_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ সাব অ্যাডমিন যোগ", "🗑 সাব অ্যাডমিন রিমুভ"],
        ["📋 সাব অ্যাডমিন লিস্ট"],
        ["🔙 ব্যাক", "🏠 মেইন মেনু"]
    ], resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ বাতিল"]], resize_keyboard=True)

def get_broadcast_keyboard():
    return ReplyKeyboardMarkup([
        ["📝 শুধু টেক্সট", "🖼 টেক্সট + ছবি"],
        ["❌ বাতিল"]
    ], resize_keyboard=True)

async def get_categories_keyboard():
    categories = await get_categories()
    keyboard = []
    row = []
    for cat_id, platform, subject, batch in categories:
        display = f"{platform} {subject} {batch}".strip()
        if not display:
            display = f"ক্যাটাগরি {cat_id}"
        row.append(KeyboardButton(f"{display}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([KeyboardButton("🏠 মেইন মেনু")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def get_courses_keyboard(category_id):
    courses = await get_category_courses(category_id)
    if not courses:
        keyboard = [[KeyboardButton("❌ কোনো কোর্স নেই")]]
        keyboard.append([KeyboardButton("🔙 ক্যাটাগরিতে ফিরুন"), KeyboardButton("🏠 মেইন মেনু")])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    keyboard = []
    row = []
    for course_id, cycle, points in courses:
        row.append(KeyboardButton(f"📘 {cycle} - {points:.1f} pt"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([KeyboardButton("🔙 ক্যাটাগরিতে ফিরুন"), KeyboardButton("🏠 মেইন মেনু")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== স্টার্ট ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref_id = int(context.args[0].replace("ref_", ""))
            if ref_id != user_id:
                async with aiosqlite.connect(DB_NAME) as db:
                    async with db.execute("SELECT 1 FROM referrals WHERE referee_id = ?", (user_id,)) as cursor:
                        existing = await cursor.fetchone()
                    
                    if not existing:
                        await db.execute(
                            "INSERT INTO referrals (referrer_id, referee_id, status) VALUES (?, ?, ?)",
                            (ref_id, user_id, "active")
                        )
                        await db.execute("UPDATE users SET points = points + 5 WHERE user_id = ?", (ref_id,))
                        await db.commit()
                        try:
                            await context.bot.send_message(
                                chat_id=ref_id,
                                text=f"🎉 <b>নতুন রেফারেল!</b>\n\n"
                                     f"👤 {html.escape(update.effective_user.full_name)} আপনার লিংক ব্যবহার করেছে!\n"
                                     f"💰 ৫ পয়েন্ট যুক্ত হয়েছে।",
                                parse_mode="HTML"
                            )
                        except:
                            pass
        except:
            pass
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, points) VALUES (?, ?, ?, ?)",
            (user_id, update.effective_user.username, update.effective_user.full_name, 5.0 if TEST_MODE else 0.0)
        )
        await db.commit()
    
    if not await is_user_joined(context, user_id):
        await send_join_verification(update, context)
        return
    
    await update.message.reply_text(
        "🏠 <b>স্টাডি রুম বটে স্বাগতম!</b>\n\n"
        "📚 কোর্স কিনতে বা রেফার করে পয়েন্ট অর্জন করতে নিচের মেনু ব্যবহার করুন:",
        reply_markup=await get_main_keyboard(user_id),
        parse_mode="HTML"
    )

# ==================== কেন্দ্রীয় টেক্সট হ্যান্ডলার ====================
async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, points) VALUES (?, ?, ?, ?)",
            (user_id, update.effective_user.username, update.effective_user.full_name, 5.0 if TEST_MODE else 0.0)
        )
        await db.commit()
    
    if not await is_user_joined(context, user_id):
        await send_join_verification(update, context)
        return

    if text == "🏠 মেইন মেনু":
        context.user_data['selected_category'] = None
        context.user_data['state'] = None
        context.user_data['admin_state'] = None
        
        if context.user_data.get('is_admin_mode') and await is_admin(user_id):
            await update.message.reply_text(
                "🛠 <b>অ্যাডমিন প্যানেল</b>\n━━━━━━━━━━━━━━━━━━",
                reply_markup=get_admin_main_keyboard(),
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                "🏠 <b>মেইন মেনু</b>", 
                reply_markup=await get_main_keyboard(user_id), 
                parse_mode="HTML"
            )
        return
    
    if text == "👑 অ্যাডমিন প্যানেল":
        if await is_admin(user_id):
            context.user_data['is_admin_mode'] = True
            await update.message.reply_text(
                "🛠 <b>অ্যাডমিন প্যানেল</b>\n━━━━━━━━━━━━━━━━━━",
                reply_markup=get_admin_main_keyboard(),
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ <b>আপনি অ্যাডমিন নন!</b>\nআইডি: <code>{user_id}</code>", parse_mode="HTML")
        return
    
    if text == "🔙 ইউজার মোড":
        context.user_data['is_admin_mode'] = False
        await update.message.reply_text("👤 <b>ইউজার মোড</b>", reply_markup=await get_main_keyboard(user_id), parse_mode="HTML")
        return
    
    if context.user_data.get('is_admin_mode') and await is_admin(user_id):
        await handle_admin_text(update, context)
        return
    
    if text == "👤 প্রোফাইল":
        await show_profile(update, context)
        return
    elif text == "🔗 রেফারেল লিংক":
        await show_referral_link(update, context)
        return
    elif text == "🏆 লিডারবোর্ড":
        await show_leaderboard(update, context)
        return
    elif text == "📂 সকল কোর্সসমূহ":
        await show_all_courses(update, context)
        return
    elif text == "📚 আমার কোর্স":
        await show_my_courses(update, context)
        return
    elif text == "🛒 কোর্স কিনুন":
        keyboard = await get_categories_keyboard()
        await update.message.reply_text(
            "📚 <b>ক্যাটাগরি নির্বাচন করুন:</b>\n\n"
            "নিচের তালিকা থেকে আপনার পছন্দের ক্যাটাগরি নির্বাচন করুন:", 
            reply_markup=keyboard, 
            parse_mode="HTML"
        )
        return
    elif text == "💬 সাপোর্ট":
        await update.message.reply_text("💬 <b>আপনার বার্তাটি লিখে পাঠান:</b>", parse_mode="HTML")
        context.user_data['state'] = 'support'
        return
    
    # ক্যাটাগরি নির্বাচন
    categories = await get_categories()
    matched = False
    for cat_id, platform, subject, batch in categories:
        display = f"{platform} {subject} {batch}".strip()
        if display == text:
            context.user_data['selected_category'] = cat_id
            keyboard = await get_courses_keyboard(cat_id)
            await update.message.reply_text(
                f"📚 <b>{html.escape(display)}</b>\n\nএকটি কোর্স নির্বাচন করুন:", 
                reply_markup=keyboard, 
                parse_mode="HTML"
            )
            matched = True
            break
    
    if matched:
        return
    
    if text.startswith("📘 "):
        await handle_course_select(update, context)
        return
    
    if text == "🔙 ক্যাটাগরিতে ফিরুন":
        keyboard = await get_categories_keyboard()
        await update.message.reply_text(
            "📚 <b>ক্যাটাগরি নির্বাচন করুন:</b>\n\n"
            "নিচের তালিকা থেকে আপনার পছন্দের ক্যাটাগরি নির্বাচন করুন:", 
            reply_markup=keyboard, 
            parse_mode="HTML"
        )
        return
    
    if context.user_data.get('state') == 'support':
        await handle_support(update, context)
        return
    
    await update.message.reply_text("❓ <b>অজানা কমান্ড!</b> মেনু থেকে নির্বাচন করুন।", parse_mode="HTML")

# ==================== ফটো হ্যান্ডলার ====================
async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return
    
    admin_state = context.user_data.get('admin_state')
    if admin_state == 'waiting_for_broadcast_photo':
        await handle_broadcast_photo(update, context)
    elif admin_state in ['bulk_images_upload', 'bulk_images_single']:
        await handle_bulk_images_upload(update, context)
    else:
        await update.message.reply_text("❌ এই মুহূর্তে ছবি পাঠানোর প্রয়োজন নেই।")

# ==================== ইউজার ফাংশনসমূহ ====================
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            points = row[0] if row else 0.0
        
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status='active'", (user_id,)) as cursor:
            ref_count = (await cursor.fetchone())[0]
        
        async with db.execute("SELECT COUNT(*) FROM user_access WHERE user_id = ? AND used = 1", (user_id,)) as cursor:
            course_count = (await cursor.fetchone())[0]
            
    msg = (
        f"👤 <b>প্রোফাইল</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 আইডি: <code>{user_id}</code>\n"
        f"👑 নাম: {html.escape(update.effective_user.full_name)}\n"
        f"💰 পয়েন্ট: <code>{points:.1f}</code>\n"
        f"👥 রেফারেল: <code>{ref_count}</code> জন\n"
        f"📚 সংগৃহীত কোর্স: <code>{course_count}</code> টি"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ref_link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
    msg = (
        f"🔗 <b>রেফারেল লিংক</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"📌 প্রতি সফল রেফারেলে পাবেন <code>৫</code> point!"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT full_name, points FROM users ORDER BY points DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await update.message.reply_text("📊 কোনো ডেটা নেই!")
        return
        
    msg = "🏆 <b>লিডারবোর্ড</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for idx, (name, pts) in enumerate(rows, 1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
        msg += f"{medal} {html.escape(name[:20])} — <code>{pts:.1f}</code> pt\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_all_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.cycle, c.points_required, cat.platform, cat.subject, cat.batch 
            FROM courses c JOIN categories cat ON c.category_id = cat.category_id 
            WHERE c.is_active = 1 ORDER BY cat.platform, cat.subject, cat.batch, c.cycle
        """) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await update.message.reply_text("📂 কোনো কোর্স পাওয়া যায়নি!")
        return
        
    msg = "📋 <b>সকল কোর্সসমূহ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    current_cat = ""
    for cycle, points, platform, subject, batch in rows:
        cat_name = f"{platform} {subject} {batch}"
        if cat_name != current_cat:
            current_cat = cat_name
            msg += f"\n📁 <b>{html.escape(cat_name)}</b>\n"
        msg += f"  • {html.escape(cycle)} — <code>{points:.1f}</code> pt\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_my_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.cycle, cat.platform, cat.subject, cat.batch, ua.used 
            FROM user_access ua JOIN courses c ON ua.course_id = c.course_id 
            JOIN categories cat ON c.category_id = cat.category_id 
            WHERE ua.user_id = ? ORDER BY ua.purchase_date DESC
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await update.message.reply_text("📂 আপনার কোনো কোর্স নেই!")
        return
        
    msg = "🎓 <b>আমার কোর্সসমূহ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for cycle, platform, subject, batch, used in rows:
        status = "✅" if used == 1 else "⏳"
        msg += f"{status} <b>{html.escape(platform)} {html.escape(subject)} {html.escape(batch)}</b> - {html.escape(cycle)}\n"
    await update.message.reply_text(msg, parse_mode="HTML")

# ==================== কোর্স সিলেক্ট ও ইনফো দেখানো ====================
async def handle_course_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    category_id = context.user_data.get('selected_category')
    
    if not category_id:
        keyboard = await get_categories_keyboard()
        await update.message.reply_text("❌ কোনো ক্যাটাগরি নির্বাচিত নেই! অনুগ্রহ করে প্রথমে ক্যাটাগরি নির্বাচন করুন:", reply_markup=keyboard)
        return
    
    try:
        cycle_display = text.split(" - ")[0].replace("📘 ", "").strip()
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("""
                SELECT c.course_id, c.cycle, c.points_required, c.image_id, c.info_text, cat.platform, cat.subject, cat.batch
                FROM courses c JOIN categories cat ON c.category_id = cat.category_id
                WHERE c.category_id = ? AND c.cycle = ? AND c.is_active = 1
            """, (category_id, cycle_display)) as cursor:
                course = await cursor.fetchone()
        
        if not course:
            keyboard = await get_courses_keyboard(category_id)
            await update.message.reply_text("❌ কোর্সটি পাওয়া যায়নি! অনুগ্রহ করে নিচের তালিকা থেকে নির্বাচন করুন:", reply_markup=keyboard)
            return
        
        course_id, cycle, points_required, image_id, info_text, platform, subject, batch = course
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
                user_points = (await cursor.fetchone())[0]
            
            async with db.execute("SELECT used FROM user_access WHERE user_id = ? AND course_id = ?", (user_id, course_id)) as cursor:
                existing = await cursor.fetchone()
        
        if existing:
            await update.message.reply_text("⚠️ <b>আপনি ইতিমধ্যে এই কোর্সটি নিয়েছেন!</b>", parse_mode="HTML")
            return
            
        remaining_points = user_points - points_required
        
        caption = (
            f"📖 <b>কোর্স বিবরণী:</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📚 <b>কোর্স:</b> {html.escape(platform)} {html.escape(subject)} {html.escape(batch)} ({html.escape(cycle)})\n"
            f"📝 <b>বিবরণ:</b>\n{html.escape(info_text or 'N/A')}\n\n"
            f"💳 <b>আপনার বর্তমান পয়েন্ট:</b> <code>{user_points:.1f}</code> pt\n"
            f"💰 <b>প্রয়োজনীয় পয়েন্ট:</b> <code>{points_required:.1f}</code> pt\n"
            f"📊 <b>কোর্সের পর অবশিষ্ট পয়েন্ট:</b> <code>{remaining_points:.1f}</code> pt\n\n"
            f"⚠️ জয়েন করতে চাইলে নিচের বাটনে ক্লিক করুন (পয়েন্ট কাটা হবে):"
        )
        
        keyboard = [[InlineKeyboardButton("🚀 জয়েন করুন (পয়েন্ট কাটুন)", callback_data=f"buy_course_{course_id}")]]
        
        if image_id:
            await update.message.reply_photo(photo=image_id, caption=caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            await update.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Course Select Error: {e}")
        await update.message.reply_text("❌ কোনো একটি সমস্যা হয়েছে! পুনরায় মেইন মেনু থেকে চেষ্টা করুন।")

# ==================== কোর্স ক্রয় কনফার্ম বাটন কলব্যাক ====================
async def buy_course_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id

    if not await is_user_joined(context, user_id):
        await send_join_verification(update, context)
        return

    course_id = int(query.data.replace("buy_course_", ""))
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.course_id, c.cycle, c.points_required, c.channel_id, cat.platform, cat.subject, cat.batch 
            FROM courses c JOIN categories cat ON c.category_id = cat.category_id
            WHERE c.course_id = ?
        """, (course_id,)) as cursor:
            course = await cursor.fetchone()
            
        async with db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user_points = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT used FROM user_access WHERE user_id = ? AND course_id = ?", (user_id, course_id)) as cursor:
            existing = await cursor.fetchone()
            
    if existing:
        await query.message.reply_text("⚠️ <b>আপনি ইতিমধ্যে এই কোর্সটি নিয়েছেন!</b>", parse_mode="HTML")
        return

    _, cycle, points_required, channel_id, platform, subject, batch = course

    if user_points < points_required:
        await query.message.reply_text(
            f"❌ <b>পর্যাপ্ত point নেই!</b>\n\n"
            f"📘 প্রয়োজন: <code>{points_required:.1f}</code> pt\n"
            f"💳 আপনার: <code>{user_points:.1f}</code> pt",
            parse_mode="HTML"
        )
        return

    try:
        invite_link_obj = await context.bot.create_chat_invite_link(chat_id=channel_id, creates_join_request=True)
        final_link = invite_link_obj.invite_link
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET points = points - ? WHERE user_id = ?", (points_required, user_id))
            await db.execute("INSERT INTO user_access (user_id, course_id, invite_link, used) VALUES (?, ?, ?, 0)", (user_id, course_id, final_link))
            await db.commit()
            
        success_msg = (
            f"✅ <b>পয়েন্ট কেটে নেওয়া হয়েছে এবং লিংক জেনারেট হয়েছে!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📚 <b>কোর্স:</b> {html.escape(platform)} {html.escape(subject)} {html.escape(batch)} ({html.escape(cycle)})\n"
            f"💰 <b>কাটা পয়েন্ট:</b> <code>{points_required:.1f}</code> pt\n\n"
            f"👇 <b>নিচের বাটনে ক্লিক করে প্রাইভেট চ্যানেলে জয়েন রিকোয়েস্ট পাঠান:</b>"
        )
        keyboard = [[InlineKeyboardButton("🚀 চ্যানেলে জয়েন করুন", url=final_link)]]
        
        await query.message.reply_text(success_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Buy Course Error: {e}")
        await query.message.reply_text("❌ সমস্যা হয়েছে! চ্যানেলের পারমিশন ঠিক আছে কিনা চেক করুন।")

# ==================== অটো জয়েন রিকোয়েস্ট ও অ্যাডমিন নোটিফিকেশন ====================
async def auto_approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_join_request = update.chat_join_request
    user = chat_join_request.from_user
    user_id = user.id
    chat_id = chat_join_request.chat.id
    
    if not chat_join_request.invite_link:
        return
        
    invite_link = chat_join_request.invite_link.invite_link.strip()
    
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("""
                SELECT ua.user_id, ua.course_id, ua.used, c.cycle, c.points_required, cat.platform, cat.subject, cat.batch 
                FROM user_access ua
                JOIN courses c ON ua.course_id = c.course_id
                JOIN categories cat ON c.category_id = cat.category_id
                WHERE ua.invite_link = ?
            """, (invite_link,)) as cursor:
                row = await cursor.fetchone()
            
            if row and row[0] == user_id and row[2] == 0:
                owner_id, course_id, used, cycle_name, points_req, platform, subject, batch = row
                bd_time_now = get_bd_time_str()

                try:
                    await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
                except TelegramError as te:
                    if "HIDE_REQUESTER_MISSING" in str(te).upper() or "BAD REQUEST" in str(te).upper():
                        return
                    else:
                        raise te

                await db.execute(
                    "UPDATE user_access SET used = 1, join_date = ? WHERE invite_link = ?", 
                    (datetime.now(), invite_link)
                )
                await db.commit()
                
                success_msg = (
                    f"✅ <b>জয়েন রিকোয়েস্ট অ্যাপ্রুভ করা হয়েছে!</b>\n\n"
                    f"🎉 আপনি এখন <b>{html.escape(platform)} {html.escape(subject)} {html.escape(batch)} ({html.escape(cycle_name)})</b> চ্যানেলে যুক্ত হয়েছেন।\n\n"
                    f"⏰ জয়েন সময়: <code>{bd_time_now}</code>\n\n"
                    f"⚠️ <b>এই লিংকটি এখন এক্সপায়ার হয়ে গেছে!</b>"
                )
                try:
                    await context.bot.send_message(chat_id=user_id, text=success_msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to send success msg: {e}")

                admin_alert = (
                    f"🛒 <b>নতুন কোর্স জয়েন নোটিফিকেশন!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 <b>ইউজার:</b> {html.escape(user.full_name)}\n"
                    f"🆔 <b>আইডি:</b> <code>{user_id}</code> (@{user.username or 'N/A'})\n"
                    f"📚 <b>কোর্স:</b> {html.escape(platform)} {html.escape(subject)} {html.escape(batch)} - {html.escape(cycle_name)}\n"
                    f"💰 <b>কাটা পয়েন্ট:</b> <code>{points_req:.1f}</code> pt\n"
                    f"⏰ <b>সময় (BD):</b> {bd_time_now}"
                )
                try:
                    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_alert, parse_mode="HTML")
                except Exception as err:
                    logger.error(f"Failed to notify admin: {err}")

            else:
                try:
                    await context.bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
                except TelegramError:
                    pass

                expired_msg = (
                    f"❌ <b>এই লিংকটি বৈধ নয়!</b>\n\n"
                    f"• লিংকটি ইতিমধ্যে ব্যবহার করা হয়েছে\n"
                    f"• অথবা এটি মেয়াদ উত্তীর্ণ"
                )
                try:
                    await context.bot.send_message(chat_id=user_id, text=expired_msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to send expired msg: {e}")

    except Exception as e:
        logger.error(f"Approve/Decline Error: {e}")

# ==================== সাপোর্ট ও চেক জয়েন ====================
async def handle_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message.text
    admin_msg = f"📩 <b>নতুন সাপোর্ট বার্তা</b>\n\n👤 {html.escape(user.full_name)} (<code>{user.id}</code>)\n📝 {html.escape(msg)}"
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode="HTML")
        await update.message.reply_text("✅ বার্তা পাঠানো হয়েছে!", reply_markup=await get_main_keyboard(user.id))
    except:
        await update.message.reply_text("❌ এরর হয়েছে!")
    context.user_data['state'] = None

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await is_user_joined(context, query.from_user.id):
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(chat_id=query.message.chat_id, text="✅ <b>ভেরিফিকেশন সফল!</b>", reply_markup=await get_main_keyboard(query.from_user.id), parse_mode="HTML")
    else:
        await query.message.reply_text("❌ <b>আপনি এখনও সব গ্রুপ/চ্যানেলে জয়েন করেননি!</b>\n\nঅনুগ্রহ করে নির্দিষ্ট গ্রুপ ও চ্যানেলে জয়েন করে '✅ জয়েন করেছি' বাটনে চাপ দিন।", parse_mode="HTML")

# ==================== অ্যাডমিন প্যানেল ====================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "🔙 ব্যাক":
        await update.message.reply_text("🛠 <b>অ্যাডমিন কন্ট্রোল প্যানেল</b>", reply_markup=get_admin_main_keyboard(), parse_mode="HTML")
        return
    elif text == "❌ বাতিল":
        context.user_data['admin_state'] = None
        await update.message.reply_text("✅ বাতিল করা হয়েছে!", reply_markup=get_admin_main_keyboard())
        return

    elif text == "📚 কোর্স ম্যানেজমেন্ট":
        await update.message.reply_text("📚 <b>কোর্স ম্যানেজমেন্ট</b>", reply_markup=get_admin_courses_keyboard(), parse_mode="HTML")
        return
    elif text == "📁 ক্যাটাগরি ম্যানেজমেন্ট":
        await update.message.reply_text("📁 <b>ক্যাটাগরি ম্যানেজমেন্ট</b>", reply_markup=get_admin_categories_keyboard(), parse_mode="HTML")
        return
    elif text == "👥 ইউজার ম্যানেজমেন্ট":
        await update.message.reply_text("👥 <b>ইউজার ম্যানেজমেন্ট</b>", reply_markup=get_admin_users_keyboard(), parse_mode="HTML")
        return
    elif text == "👑 সাব অ্যাডমিন":
        markup = get_sub_admin_super_keyboard() if await is_super_admin(user_id) else get_sub_admin_keyboard()
        await update.message.reply_text("👑 <b>সাব অ্যাডমিন প্যানেল</b>", reply_markup=markup, parse_mode="HTML")
        return
    
    elif text == "📦 ব্যাকআপ ম্যানেজমেন্ট":
        await update.message.reply_text("📦 <b>ব্যাকআপ ম্যানেজমেন্ট</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                                       "📌 এখান থেকে আপনি ডেটাবেজের ব্যাকআপ নিতে, দেখতে এবং রিস্টোর করতে পারবেন।",
                                       reply_markup=get_backup_keyboard(), parse_mode="HTML")
        return
    
    elif text == "📦 ব্যাকআপ নাও":
        backup_file = await create_database_backup()
        if backup_file:
            await update.message.reply_text(
                f"✅ <b>ব্যাকআপ তৈরি হয়েছে!</b>\n"
                f"📁 ফাইল: <code>{os.path.basename(backup_file)}</code>\n"
                f"📅 সময়: {get_bd_time_str()}",
                parse_mode="HTML",
                reply_markup=get_backup_keyboard()
            )
        else:
            await update.message.reply_text("❌ ব্যাকআপ তৈরি ব্যর্থ!", reply_markup=get_backup_keyboard())
        return
    
    elif text == "📋 ব্যাকআপ লিস্ট":
        backups = await list_all_backups()
        if not backups:
            await update.message.reply_text("📂 কোনো ব্যাকআপ পাওয়া যায়নি!", reply_markup=get_backup_keyboard())
            return
        
        msg = "📋 <b>সকল ব্যাকআপ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        for idx, b in enumerate(backups, 1):
            msg += f"{idx}. <code>{b['filename']}</code>\n"
            msg += f"   📅 {b['created'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            msg += f"   📦 {b['size']} KB\n\n"
        
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_backup_keyboard())
        return
    
    elif text == "🔄 ব্যাকআপ রিস্টোর":
        await update.message.reply_text(
            "🔄 <b>ব্যাকআপ রিস্টোর</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "আপনি যে ব্যাকআপটি রিস্টোর করতে চান তার পুরো নাম লিখুন:\n"
            "যেমন: <code>study_room_backup_20260124_120000.db</code>\n\n"
            "📌 <b>সাবধান:</b> রিস্টোর করলে বর্তমান ডেটা পরিবর্তন হবে!",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        context.user_data['admin_state'] = 'restore_backup'
        return

    elif text == "📋 সকল সাইকেল":
        await show_all_admin_cycles(update, context)
        return
    elif text == "📋 সকল ক্যাটাগরি":
        await show_all_admin_categories(update, context)
        return
    elif text == "👥 সকল ইউজার":
        await show_all_users_admin(update, context)
        return
    elif text == "📊 পারচেজ হিস্টোরি":
        await show_recent_purchases(update, context)
        return
    elif text == "📋 সাব অ্যাডমিন লিস্ট":
        await show_sub_admins(update, context)
        return

    elif text == "🔎 ইউজার ডিটেইলস":
        await update.message.reply_text("🔎 <b>ইউজারের তথ্য দেখতে User ID বা Username লিখুন:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'search_user'
        return
    elif text == "💰 point add / remove":
        await update.message.reply_text("💰 <b>পয়েন্ট যোগ/বিয়োগ করুন:</b>\nFormat: <code>ইউজার_আইডি | পয়েন্ট</code>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'add_points_user'
        return
    elif text == "➕ সাইকেল যোগ":
        await update.message.reply_text("➕ <b>নতুন সাইকেল যোগ</b>\nFormat: <code>ক্যাটাগরি_আইডি | সাইকেল | পয়েন্ট</code>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'add_cycle'
        return
    elif text == "📦 একসাথে সাইকেল যোগ":
        await update.message.reply_text("📦 <b>একসাথে সাইকেল যোগ</b>\nFormat:\n<code>ক্যাটাগরি_আইডি | সাইকেল | পয়েন্ট</code>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'bulk_add_cycles'
        return
    elif text == "📦 একসাথে ইনফো যোগ":
        await update.message.reply_text("📦 <b>একসাথে ইনফো যোগ</b>\nFormat:\n<code>সাইকেল_আইডি | ইনফো | চ্যানেল_আইডি</code>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'bulk_add_info'
        return
    elif text == "📦 একসাথে ছবি যোগ":
        await update.message.reply_text("📦 <b>একসাথে ছবি যোগ</b>\nসাইকেল আইডি লিখুন (কমা দিয়ে):", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'bulk_add_images'
        return
    elif text == "➕ ক্যাটাগরি যোগ":
        await update.message.reply_text("📁 <b>নতুন ক্যাটাগরি যোগ</b>\nFormat: <code>প্ল্যাটফর্ম | সাবজেক্ট | ব্যাচ</code>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'add_category'
        return
    elif text == "📦 একসাথে ক্যাটাগরি যোগ":
        await update.message.reply_text("📦 <b>একসাথে ক্যাটাগরি যোগ</b>\nFormat:\n<code>প্ল্যাটফর্ম | সাবজেক্ট | ব্যাচ</code>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'bulk_add_categories'
        return
    elif text == "🗑 সাইকেল ডিলিট":
        await update.message.reply_text("🗑 <b>ডিলিট করতে চাওয়া সাইকেল আইডিটি লিখুন:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'delete_cycle'
        return
    elif text == "➕ সাব অ্যাডমিন যোগ":
        await update.message.reply_text("➕ <b>নতুন সাব অ্যাডমিন আইডি লিখুন:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'add_sub_admin'
        return
    elif text == "🗑 সাব অ্যাডমিন রিমুভ":
        await update.message.reply_text("🗑 <b>রিমুভ করতে চাওয়া সাব অ্যাডমিন আইডি লিখুন:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'remove_sub_admin'
        return
    elif text == "📢 ব্রডকাস্ট":
        await update.message.reply_text("📢 <b>ব্রডকাস্ট টাইপ নির্বাচন করুন:</b>", reply_markup=get_broadcast_keyboard(), parse_mode="HTML")
        return
    elif text == "📝 শুধু টেক্সট":
        await update.message.reply_text("📝 <b>ব্রডকাস্ট মেসেজ লিখুন:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'broadcast_text'
        return
    elif text == "🖼 টেক্সট + ছবি":
        await update.message.reply_text("🖼 <b>ছবিটি পাঠান:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        context.user_data['admin_state'] = 'waiting_for_broadcast_photo'
        return

    await handle_admin_input(update, context)

# ==================== অ্যাডমিন ইনপুট সামলানো ====================
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get('admin_state')
    if not state:
        return

    if state == 'search_user':
        await search_user_details(update, context, text)
    elif state == 'add_category':
        await add_category(update, context, text)
    elif state == 'bulk_add_categories':
        await bulk_add_categories(update, context, text)
    elif state in ['add_cycle', 'bulk_add_cycles']:
        await bulk_add_cycles(update, context, text)
    elif state == 'bulk_add_info':
        await bulk_add_info(update, context, text)
    elif state == 'bulk_add_images':
        await handle_bulk_images(update, context, text)
    elif state == 'delete_cycle':
        await delete_cycle_by_id(update, context, text)
    elif state == 'add_points_user':
        await add_user_points(update, context, text)
    elif state == 'add_sub_admin':
        await add_sub_admin_logic(update, context, text)
    elif state == 'remove_sub_admin':
        await remove_sub_admin_logic(update, context, text)
    elif state == 'broadcast_text':
        await broadcast_message(update, context, text, None)
    elif state == 'waiting_for_broadcast_text':
        photo_id = context.user_data.get('broadcast_photo_id')
        await broadcast_message(update, context, text, photo_id)
    elif state == 'restore_backup':
        await handle_restore_backup(update, context, text)

# ==================== ব্যাকআপ রিস্টোর হ্যান্ডলার ====================
async def handle_restore_backup(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """ব্যাকআপ রিস্টোর করার ফাংশন"""
    if text == "❌ বাতিল":
        context.user_data['admin_state'] = None
        await update.message.reply_text("✅ বাতিল করা হয়েছে!", reply_markup=get_admin_main_keyboard())
        return
    
    backup_filename = text.strip()
    
    # চেক করুন ফাইল আছে কিনা
    backups = await list_all_backups()
    found = False
    for b in backups:
        if b['filename'] == backup_filename:
            found = True
            break
    
    if not found:
        await update.message.reply_text(
            f"❌ <code>{backup_filename}</code> নামে কোনো ব্যাকআপ পাওয়া যায়নি!\n\n"
            f"সঠিক নাম লিখুন অথবা '📋 ব্যাকআপ লিস্ট' থেকে দেখে নিন।",
            parse_mode="HTML",
            reply_markup=get_backup_keyboard()
        )
        return
    
    # রিস্টোর করুন
    success = await restore_database_from_backup(backup_filename)
    
    if success:
        await update.message.reply_text(
            f"✅ <b>ব্যাকআপ রিস্টোর সফল!</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"📁 ফাইল: <code>{backup_filename}</code>\n"
            f"📅 সময়: {get_bd_time_str()}\n\n"
            f"⚠️ বর্তমান ডেটা এই ব্যাকআপ দ্বারা প্রতিস্থাপিত হয়েছে।",
            parse_mode="HTML",
            reply_markup=get_backup_keyboard()
        )
    else:
        await update.message.reply_text(
            f"❌ <b>ব্যাকআপ রিস্টোর ব্যর্থ!</b>\n\n"
            f"📁 ফাইল: <code>{backup_filename}</code>\n"
            f"দয়া করে আবার চেষ্টা করুন।",
            parse_mode="HTML",
            reply_markup=get_backup_keyboard()
        )
    
    context.user_data['admin_state'] = None

# ==================== ইউজারের বিস্তারিত তথ্য দেখার ফাংশন ====================
async def search_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
    search_term = search_term.strip().replace("@", "")
    async with aiosqlite.connect(DB_NAME) as db:
        user_row = None
        if search_term.isdigit():
            async with db.execute("SELECT user_id, username, full_name, points, join_date FROM users WHERE user_id = ?", (int(search_term),)) as cursor:
                user_row = await cursor.fetchone()
        
        if not user_row:
            async with db.execute("SELECT user_id, username, full_name, points, join_date FROM users WHERE LOWER(username) = LOWER(?)", (search_term,)) as cursor:
                user_row = await cursor.fetchone()
                
        if not user_row:
            await update.message.reply_text("❌ কোনো ইউজার পাওয়া যায়নি!", reply_markup=get_admin_users_keyboard())
            context.user_data['admin_state'] = None
            return
            
        uid, uname, name, pts, jdate = user_row
        
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status='active'", (uid,)) as cursor:
            ref_count = (await cursor.fetchone())[0]
            
        async with db.execute("""
            SELECT cat.platform, cat.subject, cat.batch, c.cycle, ua.purchase_date, ua.used 
            FROM user_access ua 
            JOIN courses c ON ua.course_id = c.course_id 
            JOIN categories cat ON c.category_id = cat.category_id 
            WHERE ua.user_id = ? ORDER BY ua.purchase_date DESC
        """, (uid,)) as cursor:
            courses = await cursor.fetchall()

    course_list = ""
    if courses:
        for plat, subj, batch, cyc, pdate, used in courses:
            status = "✅ জয়েন করেছে" if used == 1 else "⏳ পেন্ডিং"
            course_list += f"  • {html.escape(str(plat))} {html.escape(str(subj))} {html.escape(str(batch))} ({html.escape(str(cyc))}) - {status}\n"
    else:
        course_list = "  • কোনো কোর্স কেনা হয়নি\n"

    msg = (
        f"👤 <b>ইউজারের সম্পূর্ণ ডিটেইলস</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>আইডি:</b> <code>{uid}</code>\n"
        f"📛 <b>নাম:</b> {html.escape(str(name))}\n"
        f"🔗 <b>ইউজারনেম:</b> @{html.escape(str(uname or 'N/A'))}\n"
        f"💰 <b>বর্তমান পয়েন্ট:</b> <code>{pts:.1f}</code> pt\n"
        f"👥 <b>সফল রেফারেল:</b> <code>{ref_count}</code> জন\n"
        f"📅 <b>জয়েন ডেট:</b> <code>{html.escape(str(jdate))}</code>\n\n"
        f"📚 <b>কেনা কোর্সসমূহ ({len(courses)} টি):</b>\n{course_list}"
    )
    
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_admin_users_keyboard())
    context.user_data['admin_state'] = None

# ==================== অন্যান্য অ্যাডমিন ব্যাকএন্ড লজিক ====================
async def show_recent_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT u.full_name, u.user_id, cat.platform, cat.subject, cat.batch, c.cycle, ua.purchase_date 
            FROM user_access ua 
            JOIN users u ON ua.user_id = u.user_id 
            JOIN courses c ON ua.course_id = c.course_id 
            JOIN categories cat ON c.category_id = cat.category_id 
            ORDER BY ua.purchase_date DESC LIMIT 15
        """) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await update.message.reply_text("📊 কোনো পারচেজ হিস্টোরি পাওয়া যায়নি!")
        return
        
    msg = "📊 <b>সাম্প্রতিক পারচেজ হিস্টোরি (বাংলাদেশ সময়):</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for name, uid, plat, subj, batch, cyc, pdate in rows:
        try:
            dt_obj = datetime.strptime(pdate, "%Y-%m-%d %H:%M:%S")
            bd_time = dt_obj.replace(tzinfo=pytz.UTC).astimezone(BD_TZ)
            bd_formatted_time = bd_time.strftime("%Y-%m-%d %I:%M %p")
        except:
            bd_formatted_time = pdate
            
        msg += f"👤 {html.escape(str(name))} (<code>{uid}</code>)\n📚 {html.escape(str(plat))} {html.escape(str(subj))} {html.escape(str(batch))} ({html.escape(str(cyc))})\n⏰ <code>{bd_formatted_time}</code>\n\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) >= 3:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("INSERT INTO categories (platform, subject, batch) VALUES (?, ?, ?)", (parts[0].upper(), parts[1].upper(), parts[2]))
                await db.commit()
            await update.message.reply_text("✅ ক্যাটাগরি যোগ হয়েছে!", reply_markup=get_admin_categories_keyboard())
            context.user_data['admin_state'] = None
    except Exception as e:
        await update.message.reply_text(f"❌ এরর: {e}")

async def bulk_add_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    lines = text.strip().split('\n')
    added = 0
    async with aiosqlite.connect(DB_NAME) as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                await db.execute("INSERT INTO categories (platform, subject, batch) VALUES (?, ?, ?)", (parts[0].upper(), parts[1].upper(), parts[2]))
                added += 1
        await db.commit()
    await update.message.reply_text(f"✅ {added} টি ক্যাটাগরি যোগ হয়েছে!", reply_markup=get_admin_categories_keyboard())
    context.user_data['admin_state'] = None

async def bulk_add_cycles(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    lines = text.strip().split('\n')
    added = 0
    async with aiosqlite.connect(DB_NAME) as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                cid, cycle, pts = int(parts[0]), parts[1], float(parts[2])
                async with db.execute("SELECT platform, subject, batch FROM categories WHERE category_id = ?", (cid,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        key = f"{row[0]}_{row[1]}_{row[2]}_{cycle}".lower()
                        name = f"{row[0]} {row[1]} {row[2]} {cycle}"
                        await db.execute("INSERT INTO courses (course_key, course_name, category_id, cycle, points_required) VALUES (?, ?, ?, ?, ?)", (key, name, cid, cycle, pts))
                        added += 1
        await db.commit()
    await update.message.reply_text(f"✅ {added} টি সাইকেল যোগ হয়েছে!", reply_markup=get_admin_courses_keyboard())
    context.user_data['admin_state'] = None

async def bulk_add_info(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    lines = text.strip().split('\n')
    added = 0
    async with aiosqlite.connect(DB_NAME) as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                cid, info, ch_id = int(parts[0]), parts[1], parts[2]
                await db.execute("UPDATE courses SET info_text = ?, channel_id = ? WHERE course_id = ?", (info, ch_id, cid))
                added += 1
        await db.commit()
    await update.message.reply_text(f"✅ {added} টি তথ্য যোগ হয়েছে!", reply_markup=get_admin_courses_keyboard())
    context.user_data['admin_state'] = None

async def handle_bulk_images(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    cycle_ids = re.findall(r'\d+', text)
    if not cycle_ids:
        await update.message.reply_text("❌ কোনো আইডি পাওয়া যায়নি!")
        return
    context.user_data['bulk_cycle_ids'] = [int(x) for x in cycle_ids]
    context.user_data['bulk_images_received'] = []
    await update.message.reply_text(f"📸 ক্রমানুসারে {len(cycle_ids)} টি ছবি পাঠান।", reply_markup=get_cancel_keyboard())
    context.user_data['admin_state'] = 'bulk_images_upload'

async def handle_bulk_images_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id
    cycle_ids = context.user_data.get('bulk_cycle_ids', [])
    received = context.user_data.get('bulk_images_received', [])
    received.append(photo_id)
    context.user_data['bulk_images_received'] = received
    
    if len(received) < len(cycle_ids):
        await update.message.reply_text(f"📸 {len(received)}/{len(cycle_ids)} টি প্রাপ্ত। পরের ছবি দিন:")
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            for idx, cid in enumerate(cycle_ids):
                await db.execute("UPDATE courses SET image_id = ? WHERE course_id = ?", (received[idx], cid))
            await db.commit()
        await update.message.reply_text("✅ সব ছবি আপডেট হয়েছে!", reply_markup=get_admin_courses_keyboard())
        context.user_data['admin_state'] = None

async def delete_cycle_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        cid = int(text.strip())
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM courses WHERE course_id = ?", (cid,))
            await db.commit()
        await update.message.reply_text(f"✅ সাইকেল <code>{cid}</code> ডিলিট করা হয়েছে!", reply_markup=get_admin_courses_keyboard(), parse_mode="HTML")
    except:
        await update.message.reply_text("❌ আইডি সঠিকভাবে দিন!")
    context.user_data['admin_state'] = None

async def add_user_points(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        parts = [p.strip() for p in text.split("|")]
        uid, pts = int(parts[0]), float(parts[1])
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (pts, uid))
            await db.commit()
        await update.message.reply_text(f"✅ ইউজার <code>{uid}</code>-কে {pts} পয়েন্ট যোগ/বিয়োগ করা হয়েছে!", reply_markup=get_admin_users_keyboard(), parse_mode="HTML")
    except:
        await update.message.reply_text("❌ ফরম্যাট সঠিক দিন (<code>আইডি | পয়েন্ট</code>)!", parse_mode="HTML")
    context.user_data['admin_state'] = None

async def add_sub_admin_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        sub_id = int(text.strip())
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO sub_admins (user_id, added_by) VALUES (?, ?)", (sub_id, update.effective_user.id))
            await db.commit()
        await update.message.reply_text(f"✅ <code>{sub_id}</code> সাব-অ্যাডমিন করা হলো!", reply_markup=get_admin_main_keyboard(), parse_mode="HTML")
    except:
        await update.message.reply_text("❌ সঠিক User ID লিখুন!")
    context.user_data['admin_state'] = None

async def remove_sub_admin_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        sub_id = int(text.strip())
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM sub_admins WHERE user_id = ?", (sub_id,))
            await db.commit()
        await update.message.reply_text(f"✅ <code>{sub_id}</code> রিমুভ করা হয়েছে!", reply_markup=get_admin_main_keyboard(), parse_mode="HTML")
    except:
        await update.message.reply_text("❌ সঠিক User ID লিখুন!")
    context.user_data['admin_state'] = None

async def show_all_admin_cycles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.course_id, c.cycle, c.points_required, cat.platform, cat.subject, cat.batch 
            FROM courses c JOIN categories cat ON c.category_id = cat.category_id
        """) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("📋 কোনো সাইকেল পাওয়া যায়নি!")
        return
    msg = "📋 <b>সকল সাইকেল তালিকা:</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for cid, cycle, pts, plat, subj, batch in rows:
        msg += f"🆔 <code>{cid}</code> | <b>{html.escape(str(plat))} {html.escape(str(subj))} {html.escape(str(batch))}</b> - {html.escape(str(cycle))} (<code>{pts}</code> pt)\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_all_admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = await get_categories()
    if not categories:
        await update.message.reply_text("📋 কোনো ক্যাটাগরি পাওয়া যায়নি!")
        return
    msg = "📁 <b>সকল ক্যাটাগরি:</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for cat_id, platform, subject, batch in categories:
        msg += f"🆔 <code>{cat_id}</code> | <b>{html.escape(str(platform))}</b> - {html.escape(str(subject))} ({html.escape(str(batch))})\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_all_users_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, full_name, points FROM users LIMIT 25") as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("👥 কোনো ইউজার পাওয়া যায়নি!")
        return
    msg = "👥 <b>ইউজার তালিকা (প্রথম ২৫ জন):</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for uid, name, pts in rows:
        msg += f"🆔 <code>{uid}</code> | {html.escape(str(name))} | 💰 <code>{pts}</code> pt\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def show_sub_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM sub_admins") as cursor:
            rows = await cursor.fetchall()
    msg = f"👑 <b>সুপার অ্যাডমিন:</b> <code>{ADMIN_ID}</code>\n\n📋 <b>সাব-অ্যাডমিনগণ:</b>\n"
    if rows:
        for (uid,) in rows:
            msg += f"• <code>{uid}</code>\n"
    else:
        msg += "কোনো সাব-অ্যাডমিন যুক্ত নেই।"
    await update.message.reply_text(msg, parse_mode="HTML")

async def handle_broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id
    context.user_data['broadcast_photo_id'] = photo_id
    await update.message.reply_text("📝 <b>এখন টেক্সটটি পাঠান:</b>", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    context.user_data['admin_state'] = 'waiting_for_broadcast_text'

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, photo_id: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    sent = 0
    for (uid,) in users:
        try:
            if photo_id:
                await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=text)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.04)
        except:
            pass
    await update.message.reply_text(f"✅ {sent} জনের কাছে বার্তা পাঠানো হয়েছে!", reply_markup=get_admin_main_keyboard())
    context.user_data['admin_state'] = None

# ==================== অটো ব্যাকআপ লুপ ====================
async def auto_backup_loop():
    """প্রতি ৬ ঘণ্টা পর পর ব্যাকআপ নেয়"""
    while True:
        await asyncio.sleep(21600)  # ৬ ঘণ্টা
        await create_database_backup()
        print("🔄 Auto backup completed")

# ==================== মূল প্রোগ্রাম (MAIN) ====================
def main():
    # 🌐 Background Web Server
    threading.Thread(target=run_web_server, daemon=True).start()

    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.post_init = init_db
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(buy_course_callback, pattern="^buy_course_"))
    app.add_handler(ChatJoinRequestHandler(auto_approve_join_request))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))
    
    # 🔥 অটো ব্যাকআপ লুপ শুরু করুন
    asyncio.create_task(auto_backup_loop())
    
    print("🚀 Study Room Bot Started Successfully!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
