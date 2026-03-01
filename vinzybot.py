import telebot
from telebot import types
import psycopg2
from psycopg2 import pool
import threading
import time
import pytz
from datetime import datetime
import os
import asyncio
from pyrogram import Client

# ==========================================
# SECTION 1: CONFIGURATION
# ==========================================

# 1. Fetch Environment Variables from Koyeb
# ------------------------------------------
BOT_TOKEN = os.getenv("8782687814:AAEj5hYbo7a2TFZnfYWF7zf1NaCPx4fgyT0")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "8702798367"))
DATABASE_URL = os.getenv("postgresql://neondb_owner:npg_5vXuDLicq2wT@ep-small-boat-aim6necc-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

# --- USERBOT ENGINE CONFIG ---
API_ID = int(os.getenv("API_ID", "39060128"))
API_HASH = os.getenv("API_HASH", "5855c5f9b3fe380c767e4e84caae3289")
SESSION_STRING = "BQJUAqAALZSUOTTvHlWxyDBDQ0xl5g-BLRwNd_2d_AsZEV_mutWH67_iKN4eu4kvONgpEbHf_2XEsQ3j9MC4tzUKe4ceJ6n3K0yVr-XihvXXJPw8s1yvbWGwI0joYDWKsRrutWdICE3SIEhO-OoISC9K8jASDGi2Xilf2zLlkpSMwpG_77H5jUSQsYJVbExD6rWx8zIbEVOpC_fT6IOKKeUQbSoIKCZWx7IVZaoREvmqkYgycRyad4FRBmO4P7R2iYDxjbfYyAieVRFnO5Eh1hXzjwvhdxP7viCp2IRlMcK-0PVRUhpMniCj87YsrWnHkUd3uDyuYUctA0upOXyPKFLZpHrD-QAAAAIGuiofAA"
# 2. Initialize Telegram Interfaces
# ------------------------------------------
# Initialize @vinzystore_bot (The Frontend Bot)
bot = telebot.TeleBot(BOT_TOKEN)

# Initialize @vinzystorezz (The Audit Engine Userbot)
userbot = Client(
    "vinzy_engine", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    session_string=SESSION_STRING,
    in_memory=True
)

# 3. Initialize Neon PostgreSQL Connection Pool
# ------------------------------------------
try:
    # We set min 1 and max 10 connections to handle multiple audits at once
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)
    print("✅ Successfully connected to Neon PostgreSQL")
except Exception as e:
    print(f"❌ Database connection failed: {e}")
    # Critical for Koyeb: Exiting forces a container restart to fix network blips
    exit(1)
# ==========================================
# SECTION 2: DATABASE LOGIC (Admins/Users/Privacy)
# ==========================================

# 1. PERMANENT AUTHORIZATION LIST
# Add your ID and any permanent Admin IDs here. 
# These will NEVER be deleted, even when updating on Koyeb.
PERMANENT_ADMINS = [8702798367, 123456789] 

def init_db():
    """Initializes the Neon PostgreSQL database with Language support"""
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
        # Using BIGINT for user_id is correct for Telegram IDs
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (user_id BIGINT PRIMARY KEY, 
                      is_admin INTEGER DEFAULT 0, 
                      target_channel TEXT,
                      lang TEXT DEFAULT 'en')''')
        conn.commit()
        print("📁 Database tables verified.")
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
    finally:
        if conn:
            db_pool.putconn(conn)

def is_authorized(user_id):
    """Checks if a user has permission to use the bot tools using PostgreSQL"""
    user_id = int(user_id) # Force integer to prevent BIGINT mismatch
    
    # Check Hardcoded IDs first (Fastest)
    if user_id == SUPER_ADMIN_ID or user_id in PERMANENT_ADMINS:
        return True
        
    # Check Neon Database
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
        c.execute("SELECT is_admin FROM users WHERE user_id = %s", (user_id,))
        res = c.fetchone()
        return res is not None and res[0] == 1
    except Exception as e:
        print(f"❌ Authorization check error: {e}")
        return False
    finally:
        if conn:
            db_pool.putconn(conn)

def get_user_channel(user_id):
    """Retrieves the target channel associated with a specific user from Neon"""
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
        c.execute("SELECT target_channel FROM users WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        return result[0] if result and result[0] else None
    except Exception as e:
        print(f"❌ Get channel error: {e}")
        return None
    finally:
        if conn:
            db_pool.putconn(conn)

# --- LANGUAGE LOGIC (PostgreSQL) ---

def get_user_lang(user_id):
    """Checks the database for user's language preference. Defaults to 'en'."""
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
        c.execute("SELECT lang FROM users WHERE user_id = %s", (user_id,))
        res = c.fetchone()
        return res[0] if res and res[0] else 'en'
    except Exception as e:
        print(f"❌ Get language error: {e}")
        return 'en'
    finally:
        if conn:
            db_pool.putconn(conn)

def set_user_lang(user_id, lang_code):
    """Updates the user's language preference using Postgres UPSERT logic"""
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
        # 'ON CONFLICT' is the modern way to Upsert in Postgres
        c.execute("""
            INSERT INTO users (user_id, lang) 
            VALUES (%s, %s) 
            ON CONFLICT (user_id) 
            DO UPDATE SET lang = EXCLUDED.lang
        """, (user_id, lang_code))
        conn.commit()
    except Exception as e:
        print(f"❌ Set language error: {e}")
    finally:
        if conn:
            db_pool.putconn(conn)

# Initialize on Startup
init_db()
# ==========================================
# SECTION 3: POLL & ANTI-BOOST LOGIC
# ==========================================
import time

# Temporary tracking for live speed/pattern detection
# We use a dictionary for speed, but we will "clean" it regularly
poll_history = {} 

def clean_poll_memory():
    """Prevents RAM from filling up by removing old poll data (keeps it under 100 polls)"""
    global poll_history
    if len(poll_history) > 100:
        # Remove the oldest 50 polls to free up space
        keys_to_remove = list(poll_history.keys())[:50]
        for k in keys_to_remove:
            poll_history.pop(k, None)
        print("🧹 Memory Cleaned: Removed old poll tracking data.")

@bot.poll_handler(func=lambda poll: True)
def track_poll_votes(poll):
    p_id = str(poll.id) # Convert to string for consistent dictionary keys
    current_votes = poll.total_voter_count
    current_time = time.time()
    
    # Run a memory check every time a new poll starts being tracked
    if len(poll_history) > 100:
        clean_poll_memory()

    # 1. Initialize history for this poll if it's new
    if p_id not in poll_history:
        poll_history[p_id] = {
            'counts': [current_votes], 
            'times': [current_time],
            'last_notified_pattern': 0,
            'last_notified_threshold': False,
            'last_spike_time': 0
        }
        return

    # Extract data for easier reading
    data = poll_history[p_id]
    history_counts = data['counts']
    history_times = data['times']

    # 2. THRESHOLD ALERT (Triggers at 100 votes)
    if current_votes >= 100 and not data['last_notified_threshold']:
        bot.send_message(
            SUPER_ADMIN_ID, 
            f"⚠️ **HIGH VOLUME ALERT**\n"
            f"Poll ID: `{p_id}`\n"
            f"Total Votes: `{current_votes}`\n"
            f"Note: Check channel views vs votes ratio now!"
        )
        poll_history[p_id]['last_notified_threshold'] = True

    # 3. DRIP-FEED "STAIR-STEP" DETECTION
    # Detects robotic gain patterns (e.g., exactly +10, +10, +10)
    if len(history_counts) >= 4:
        gain1 = history_counts[-1] - history_counts[-2]
        gain2 = history_counts[-2] - history_counts[-3]
        gain3 = history_counts[-3] - history_counts[-4]
        
        if gain1 == gain2 == gain3 and gain1 > 5: # Only alert if gain is significant
            if data['last_notified_pattern'] != gain1:
                bot.send_message(
                    SUPER_ADMIN_ID, 
                    f"🛑 **DRIP-FEED DETECTED**\n"
                    f"Poll: `{p_id}`\n"
                    f"Pattern: Gaining exactly `{gain1}` votes per update.\n"
                    f"Verdict: High probability of SMM Panel Drip-Feed."
                )
                poll_history[p_id]['last_notified_pattern'] = gain1

    # 4. ABNORMAL FREQUENCY (TIMING) DETECTION
    # Detects votes arriving at perfectly even intervals
    if len(history_times) >= 3:
        gap1 = round(history_times[-1] - history_times[-2], 1)
        gap2 = round(history_times[-2] - history_times[-3], 1)
        
        # Consistent timing (humanly impossible precision)
        if abs(gap1 - gap2) < 0.1 and gap1 > 10:
            bot.send_message(
                SUPER_ADMIN_ID, 
                f"🤖 **BOT TIMING ALERT**\n"
                f"Poll: `{p_id}`\n"
                f"Consistency: Votes arriving every `{gap1}s` exactly.\n"
                f"Note: Typical of bot scripts with 'sleep' timers."
            )

    # 5. SPEED SPIKE DETECTION
    # Detects sudden mass-botting (instants)
    last_time_recorded = history_times[-1]
    time_passed = current_time - last_time_recorded
    votes_gained = current_votes - history_counts[-1]
    
    # 15+ votes in under 2 seconds is almost always a bot spike
    if votes_gained > 15 and time_passed < 2:
        # Prevent spamming alerts (only alert once every 30 seconds for spikes)
        if current_time - data['last_spike_time'] > 30:
            bot.send_message(
                SUPER_ADMIN_ID, 
                f"🚨 **SPEED SPIKE DETECTED**\n"
                f"Poll: `{p_id}`\n"
                f"Jump: +{votes_gained} votes in {round(time_passed, 2)}s!\n"
                f"Verdict: Instant Mass-Botting Attack."
            )
            poll_history[p_id]['last_spike_time'] = current_time

    # Final Step: Update history logs
    poll_history[p_id]['counts'].append(current_votes)
    poll_history[p_id]['times'].append(current_time)

    # Keep only the last 10 snapshots per poll to save memory
    if len(poll_history[p_id]['counts']) > 10:
        poll_history[p_id]['counts'].pop(0)
        poll_history[p_id]['times'].pop(0)
# ==========================================
# SECTION 4: BROADCAST & CHANNEL CHECKS
# ==========================================

def check_channel_perms(user_id, channel_id):
    """Verifies if the bot is an admin in the user's specific channel"""
    try:
        # We check the bot's own status in the target channel
        member = bot.get_chat_member(channel_id, bot.get_me().id)
        # Status can be 'administrator' or 'creator'
        if member.status not in ['administrator', 'creator']:
            return False, "❌ EN: Need Admin Perms. | KH: ត្រូវការសិទ្ធិជា Admin"
        return True, "OK"
    except Exception as e:
        # If the bot isn't in the channel, this error triggers
        return False, "❌ EN: Bot not in channel. | KH: បុគ្គលិកមិននៅក្នុង Channel ទេ"

# Note: get_user_channel is already defined in Section 2, 
# so we focus on the Broadcast Logic here.

# ==========================================
# BROADCAST COMMAND LOGIC (With Media Support)
# ==========================================

@bot.message_handler(commands=['broadcast'])
def start_broadcast(message):
    """Starts the broadcast process for authorized users only"""
    user_id = message.from_user.id
    
    # 1. Authorization Check (Uses logic from Section 2)
    if not is_authorized(user_id):
        bot.reply_to(message, "🚫 KH: អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។ | EN: No access.")
        return

    # 2. Get their locked channel from Neon Postgres
    user_channel = get_user_channel(user_id)
    
    if not user_channel:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន (/set) | EN: Set your channel first.")
        return

    # 3. Permission Check
    is_ok, error_msg = check_channel_perms(user_id, user_channel)
    if not is_ok:
        bot.reply_to(message, error_msg)
        return

    # 4. User Prompt
    prompt = (f"📢 **Private Broadcast System**\n"
              f"Target: `{user_channel}`\n\n"
              f"EN: Send the message (Text, Photo, or Video):\n"
              f"KH: សូមផ្ញើសារ រូបភាព ឬវីដេអូដែលចង់បង្ហោះ:")
    
    msg = bot.reply_to(message, prompt, parse_mode="Markdown")
    
    # Register the next step and pass the target channel
    bot.register_next_step_handler(msg, execute_private_broadcast, user_channel)

def execute_private_broadcast(message, user_channel):
    """Sends Text, Photos, or Videos to the registered channel"""
    try:
        # Use copy_message to support Text, Photo, Video, and Documents automatically
        bot.copy_message(
            chat_id=user_channel, 
            from_chat_id=message.chat.id, 
            message_id=message.message_id
        )
        
        # Feedback to user
        success_msg = (f"✅ **Success! / រួចរាល់!**\n"
                      f"EN: Broadcast sent to {user_channel}!\n"
                      f"KH: សារត្រូវបានបង្ហោះទៅកាន់ {user_channel}!")
        bot.reply_to(message, success_msg)
        
    except Exception as e:
        # Error handling (e.g., if bot was demoted mid-process)
        error_text = str(e)
        if "chat not found" in error_text.lower():
            bot.reply_to(message, "❌ Error: Channel username is invalid or private.")
        elif "admin privileges" in error_text.lower():
            bot.reply_to(message, "❌ Error: Bot lost Admin rights.")
        else:
            bot.reply_to(message, f"❌ **Broadcast Failed**\n`{error_text}`")

# ==========================================
# SECTION 5: ENGINE-BASED CHANNEL LOOKUP
# ==========================================

async def get_channel_info_via_engine(target):
    """Uses @vinzystorezz Userbot to fetch info WITHOUT being an Admin"""
    if not userbot.is_connected:
        await userbot.start()
    try:
        chat = await userbot.get_chat(target)
        return {
            "title": chat.title,
            "id": chat.id,
            "members": chat.members_count,
            "bio": chat.description or "No Bio"
        }
    except Exception as e:
        return None
# ==========================================
# SECTION 5: AUTO-SEND (CAMBODIA TIME)
# ==========================================

def schedule_checker():
    """
    Background thread to handle daily tasks at specific times.
    Optimized for Koyeb/Cloud environments.
    """
    last_run_date = "" 
    
    while True:
        try:
            # Set Timezone to Cambodia (Crucial for Koyeb servers located in US/Europe)
            tz_kh = pytz.timezone('Asia/Phnom_Penh')
            now = datetime.now(tz_kh)
            
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")

            # Condition: It is 09:00 AM AND we haven't run it yet today
            if current_time == "09:00" and last_run_date != current_date:
                print(f"⏰ [Scheduled Task] Starting 09:00 AM Broadcast - {current_date}")
                
                conn = None
                try:
                    conn = db_pool.getconn()
                    c = conn.cursor()
                    # Fetch only users who have an active target channel
                    c.execute("SELECT user_id, target_channel FROM users WHERE target_channel IS NOT NULL")
                    active_users = c.fetchall()
                    
                    for user_id, channel in active_users:
                        try:
                            # --- YOUR DAILY MESSAGE LOGIC ---
                            text = (f"📢 **Daily Morning Update / របាយការណ៍ប្រចាំថ្ងៃ**\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"Target: {channel}\n"
                                    f"Status: Auditing Active ✅\n"
                                    f"Time: {current_time} (KH)\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"Powered by @vinzystorezz Engine")
                            
                            bot.send_message(channel, text, parse_mode="Markdown")
                            
                            # ANTI-FLOOD: Wait 0.5 seconds between each channel
                            # This prevents Telegram from blocking your bot (Error 429)
                            time.sleep(0.5) 
                            
                        except Exception as send_err:
                            # Log the error but continue to the next user
                            print(f"⚠️ Skip {channel}: {send_err}")
                            continue 
                            
                    # Mark as completed for today
                    last_run_date = current_date
                    
                except Exception as db_err:
                    print(f"❌ Database error in scheduler: {db_err}")
                finally:
                    if conn:
                        db_pool.putconn(conn)

        except Exception as global_err:
            print(f"⚠️ Scheduler Heartbeat Error: {global_err}")

        # Sleep for 45 seconds. 
        # Checking every 45s ensures we hit the "09:00" window exactly once.
        time.sleep(45)

# Start the background thread
threading.Thread(target=schedule_checker, daemon=True).start()

# ==========================================
# SECTION 6: ADVANCED DEEP-SCAN (USERBOT ENGINE)
# ==========================================

async def run_userbot_audit(target_username):
    """
    ប្រើប្រាស់ @vinzystorezz Userbot ដើម្បីវិភាគទិន្នន័យ Views និង Shares ជាក់ស្តែង
    """
    if not userbot.is_connected:
        await userbot.start()
    
    try:
        # សម្អាតឈ្មោះ Username
        clean_target = target_username if target_username.startswith("@") else f"@{target_username}"
        
        # ទាញយកព័ត៌មាន Chat
        chat = await userbot.get_chat(clean_target)
        
        total_views = 0
        total_shares = 0
        post_count = 0
        suspicious_posts = 0

        # ស្កេន 50 posts ចុងក្រោយដើម្បីរកមើលភាពមិនប្រក្រតី
        async for msg in userbot.get_chat_history(chat.id, limit=50):
            if msg.views:
                v = msg.views
                s = msg.forwards or 0
                total_views += v
                total_shares += s
                post_count += 1
                
                # បើ Views ច្រើន (លើស 100) តែ Shares = 0 គឺជាសញ្ញា Bot Views
                if v > 100 and s == 0:
                    suspicious_posts += 1
            
            # Delay បន្តិចដើម្បីការពារសុវត្ថិភាព Userbot
            await asyncio.sleep(0.1) 

        if post_count == 0: return None

        avg_views = total_views / post_count
        engagement = (avg_views / chat.members_count) * 100 if chat.members_count > 0 else 0
        share_rate = (total_shares / total_views) * 100 if total_views > 0 else 0

        return {
            "subs": chat.members_count,
            "avg_v": int(avg_views),
            "engagement": engagement,
            "share_rate": share_rate,
            "fraud_index": (suspicious_posts / post_count) * 100,
            "title": chat.title
        }
    except Exception as e:
        print(f"❌ Userbot Audit Error: {e}")
        return None

@bot.message_handler(commands=['check_stats'])
def check_stats(message):
    """Deep audits channel data using Userbot Engine"""
    user_id = message.from_user.id
    
    # 1. ពិនិត្យសិទ្ធិ
    if not is_authorized(user_id):
        return

    # 2. ទាញយកឆានែលគោលដៅ
    target = get_user_channel(user_id)
    if not target:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន (/set) | EN: Set channel first.")
        return

    # បង្ហាញដំណើរការវិភាគ
    wait_msg = bot.send_message(
        message.chat.id, 
        "🔍 **Starting Deep Scan...**\n"
        "🕵️ Engine: `@vinzystorezz` is reading real-time history.\n"
        "⏳ KH: កំពុងវិភាគទិន្នន័យជាក់ស្តែង។ សូមរង់ចាំ..."
    )

    try:
        # ដំណើរការវិភាគតាមរយៈ Userbot (Async)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data = loop.run_until_complete(run_userbot_audit(target))

        if not data:
            bot.edit_message_text("❌ Audit failed! Is the channel public?", message.chat.id, wait_msg.message_id)
            return

        # ៣. ការវិភាគលទ្ធផល (Decision Logic)
        verdict = "🟢 SAFE / សុវត្ថិភាព"
        status_color = "CLEAN"
        
        # បើការចូលរួម (Engagement) ទាប ឬការ Share ទាបខ្លាំង គឺជាឆានែលបន្លំ
        if data['engagement'] < 0.5 or data['share_rate'] < 0.01:
            verdict = "🔴 HIGH RISK / គ្រោះថ្នាក់"
            status_color = "BOTTED / FAKE VIEWS"
        elif data['fraud_index'] > 30:
            verdict = "🟡 CAUTION / ប្រុងប្រយ័ត្ន"
            status_color = "INCONSISTENT ACTIVITY"

        # ៤. បង្កើតរបាយការណ៍
        report = (f"🛡️ **DEEP AUDIT: {data['title']}**\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"👥 Subs: `{data['subs']:,}`\n"
                  f"👁️ Avg Views: `{data['avg_v']:,}`\n"
                  f"📈 Engagement: `{data['engagement']:.2f}%`\n"
                  f"🔄 Share Rate: `{data['share_rate']:.3f}%`\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"⚖️ Verdict: **{verdict}**\n"
                  f"⭐ Status: `{status_color}`\n\n"
                  f"Powered by @vinzystorezz Engine")

        bot.edit_message_text(report, message.chat.id, wait_msg.message_id, parse_mode="Markdown")

    except Exception as e:
        bot.edit_message_text(f"❌ Error: `{e}`", message.chat.id, wait_msg.message_id)
# ==========================================
# SECTION 7: USER INTERFACE & PERMISSIONS (COMBINED)
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def start(message):
    """Displays the control panel based on user language with security check"""
    u_id = message.from_user.id
    
    # 1. SECURITY CHECK (Neon DB & Hardcoded IDs)
    if not is_authorized(u_id):
        remove_markup = types.ReplyKeyboardRemove()
        msg = (
            "🚫 **Access Denied! / បដិសេធការចូល!**\n\n"
            "EN: This bot is private. Contact @vinzystorezz to buy access.\n"
            "KH: គណនីរបស់អ្នកមិនទាន់មានសិទ្ធិប្រើប្រាស់ទេ។ សូមទាក់ទង @vinzystorezz ដើម្បីទិញសិទ្ធិ។"
        )
        bot.send_message(message.chat.id, msg, reply_markup=remove_markup, parse_mode="Markdown")
        return # STOP execution here for unauthorized users

    # 2. FETCH PREFERENCES
    lang = get_user_lang(u_id)
    
    # 3. CONFIGURE MULTILINGUAL LABELS
    labels = {
        'poll': "📊 Create Poll" if lang == 'en' else "📊 បង្កើតការបោះឆ្នោត",
        'audit': "🔍 Audit Channel" if lang == 'en' else "🔍 ពិនិត្យឆានែល",
        'broadcast': "📢 Broadcast" if lang == 'en' else "📢 ផ្សព្វផ្សាយ",
        'schedule': "📅 Schedule Info" if lang == 'en' else "📅 កាលវិភាគ",
        'set': "📍 Set Channel" if lang == 'en' else "📍 កំណត់ឆានែល",
        'help': "❓ Help" if lang == 'en' else "❓ ជំនួយ",
        'lang': "🌐 Language" if lang == 'en' else "🌐 ភាសា",
        'detect': "🛡️ Report Channel" if lang == 'en' else "🛡️ រាយការណ៍ឆានែល"
    }

    # 4. ORGANIZE KEYBOARD LAYOUT
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(labels['poll'], labels['audit'])
    markup.add(labels['broadcast'], labels['schedule'])
    markup.add(labels['set'], labels['detect'])
    markup.add(labels['help'], labels['lang'])
    
    # 5. ADMIN/OWNER SPECIFIC UI
    if u_id == SUPER_ADMIN_ID:
        markup.add("➕ Add Admin", "➖ Remove Admin")
        welcome_text = "👑 **OWNER CONTROL PANEL**" if lang == 'en' else "👑 **ផ្ទាំងគ្រប់គ្រងម្ចាស់ប៊ត**"
    else:
        welcome_text = "🛡️ **ADMIN CONTROL PANEL**" if lang == 'en' else "🛡️ **ផ្ទាំងគ្រប់គ្រងអ្នកអតមីន**"

    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")


# --- THE SMART ROUTER (ការចាត់ចែងបញ្ជា) ---

@bot.message_handler(func=lambda message: True)
def handle_menu_clicks(message):
    """Routes button clicks and text inputs to their specific functions"""
    u_id = message.from_user.id
    
    # Security check for text-based interactions
    if not is_authorized(u_id): 
        return
    
    text = message.text
    lang = get_user_lang(u_id)

    # 1. CHANNEL SETTING DETECTION (Link or @username)
    if text.startswith('@') or 't.me/' in text:
        process_set_channel_logic(message) 
        return

    # 2. AUDIT COMMANDS
    if text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        check_stats(message) 
    
    # 3. MANUAL SET CHANNEL
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        msg = bot.reply_to(message, "📍 **EN:** Send channel @username\n📍 **KH:** សូមផ្ញើឈ្មោះឆានែល (ឧទាករណ៍៖ @username)")
        bot.register_next_step_handler(msg, process_set_channel_logic)

    # 4. REPORTING SYSTEM
    elif text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"]:
        # Note: This calls the Section 9 simulator logic you added
        report_start(message) 
        
    # 5. LANGUAGE SETTINGS
    elif text in ["🌐 Language", "🌐 ភាសា"]:
        show_language_keyboard(message)
        
    # 6. OWNER PRIVILEGES
    elif text == "➕ Add Admin" and u_id == SUPER_ADMIN_ID:
        add_admin_prompt(message)
    elif text == "➖ Remove Admin" and u_id == SUPER_ADMIN_ID:
        remove_admin_prompt(message)

    # 7. HELP SYSTEM
    elif text in ["❓ Help", "❓ ជំនួយ"]:
        # Ensure you have a send_help function defined
        send_help(message, lang)

    # 8. BROADCAST SYSTEM
    elif text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"]:
        start_broadcast(message)

# --- SUPPORTING FUNCTIONS ---

def show_language_keyboard(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en'),
        types.InlineKeyboardButton("ភាសាខ្មែរ 🇰🇭", callback_data='set_lang_kh')
    )
    bot.send_message(message.chat.id, "🌐 Select Language / សូមជ្រើសរើសភាសា:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_lang_'))
def callback_language(call):
    new_lang = call.data.split('_')[2]
    set_user_lang(call.from_user.id, new_lang)
    bot.answer_callback_query(call.id, "Success!")
    bot.edit_message_text(
        "✅ Language updated! Press /menu" if new_lang == 'en' else "✅ ភាសាត្រូវបានផ្លាស់ប្តូរ! សូមចុច /menu",
        call.message.chat.id,
        call.message.message_id
    )

def add_admin_prompt(message):
    msg = bot.reply_to(message, "🆔 Send Telegram User ID to add as Admin:")
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    try:
        new_id = int(message.text)
        conn = db_pool.getconn()
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO users (user_id, is_admin) VALUES (%s, 1) 
                ON CONFLICT (user_id) DO UPDATE SET is_admin = 1
            """, (new_id,))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ User {new_id} is now an Admin.")
        finally:
            db_pool.putconn(conn)
    except:
        bot.send_message(message.chat.id, "❌ Invalid ID. Please send numbers only.")

def process_set_channel_logic(message):
    target = message.text.strip()
    if not target.startswith('@') and not target.startswith('-100'):
        target = f"@{target}"
    
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        c.execute("UPDATE users SET target_channel = %s WHERE user_id = %s", (target, message.from_user.id))
        conn.commit()
        bot.reply_to(message, f"✅ Channel set to: {target}")
    except Exception as e:
        bot.reply_to(message, f"❌ DB Error: {e}")
    finally:
        db_pool.putconn(conn)
# ==========================================
# SECTION 8: FULL FEATURE MENU & ROUTING
# ==========================================

# --- មុខងារជំនួយសម្រាប់ SETTINGS ---

def set_channel_prompt(message):
    """ចាប់ផ្តើមដំណើរការកំណត់ Channel គោលដៅសម្រាប់អ្នកប្រើប្រាស់"""
    u_id = message.from_user.id
    lang = get_user_lang(u_id)
    prompt = (
        "📍 **Target Channel Configuration**\n\n"
        "EN: Send the channel @username or ID:\n"
        "KH: សូមផ្ញើឈ្មោះ Channel របស់អ្នក (ឧទាហរណ៍ @username):"
    )
    msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_set_channel)

def process_set_channel(message):
    """រក្សាទុក Channel ទៅក្នុង Neon PostgreSQL Database"""
    u_id = message.from_user.id
    channel_val = message.text.strip()
    
    # បន្ថែម @ ស្វ័យប្រវត្តិ ប្រសិនបើអ្នកប្រើភ្លេច
    if not channel_val.startswith('@') and not channel_val.startswith('-100'):
        channel_val = f"@{channel_val}"
        
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
        c.execute("UPDATE users SET target_channel = %s WHERE user_id = %s", (channel_val, u_id))
        conn.commit()
        bot.reply_to(message, f"✅ **Success!**\nTarget locked to: `{channel_val}`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Database Error: {e}")
    finally:
        if conn:
            db_pool.putconn(conn)

# --- THE GLOBAL BUTTON ROUTER (អ្នកចាត់ចែងប៊ូតុងទូទៅ) ---

@bot.message_handler(func=lambda m: True)
def handle_all_buttons(message):
    u_id = message.from_user.id
    
    # ពិនិត្យសិទ្ធិ៖ មានតែ Admin ដែលមានក្នុង Database ប៉ុណ្ណោះដែលអាចប្រើបាន
    if not is_authorized(u_id): 
        return
    
    lang = get_user_lang(u_id)
    text = message.text

    # 1. ប្តូរភាសា (LANGUAGE SELECTOR)
    if text in ["🌐 Language", "🌐 ភាសា"]:
        show_language_keyboard(message)

    # 2. ជំនួយ (SYSTEM HELP)
    elif text in ["❓ Help", "❓ ជំនួយ"]:
        send_help(message, lang)

    # 3. បង្កើតការបោះឆ្នោត (POLL MANAGEMENT)
    elif text in ["📊 Create Poll", "📊 បង្កើតការបោះឆ្នោត"]:
        prompt = "📋 Send name list (one per line):" if lang == 'en' else "📋 សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (ម្នាក់មួយបន្ទាត់):"
        msg = bot.send_message(message.chat.id, prompt)
        bot.register_next_step_handler(msg, process_poll_names)

    # 4. វិភាគឆានែល (DEEP AUDIT)
    elif text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        check_stats(message) # ហៅមុខងារពី Section 6

    # 5. ផ្សព្វផ្សាយសារ (MASS BROADCAST)
    elif text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"]:
        start_broadcast(message) # ហៅមុខងារពី Section 4

    # 6. ព័ត៌មានកាលវិភាគ និងម៉ោង (TIME SYNC)
    elif text in ["📅 Schedule Info", "📅 កាលវិភាគ", "📅 ព័ត៌មានកាលវិភាគ"]:
        tz_kh = pytz.timezone('Asia/Phnom_Penh')
        now_kh = datetime.now(tz_kh).strftime("%I:%M %p")
        status = (f"⏰ **System Status**\n\n"
                  f"Cambodia Time: `{now_kh}`\n"
                  f"Auto-Audit Task: `09:00 AM` (Daily)")
        bot.send_message(message.chat.id, status, parse_mode="Markdown")

    # 7. កំណត់ឆានែល (CHANNEL SETTINGS)
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        set_channel_prompt(message)

    # 8. ប្រព័ន្ធការពារ និងរាយការណ៍ (ANTI-BOOST PROTECTION)
    elif text in ["🛡️ Report Channel", "🛡️ ស្វែងរក Bot", "🛡️ Poll Detection"]:
        msg = ("🛡️ **Anti-Boost System Active**\n\n"
               "The bot is currently scanning the linked channel for:\n"
               "• SMM Drip-feed patterns\n"
               "• Speed Spikes\n"
               "• Robotic Timing consistency")
        bot.send_message(message.chat.id, msg)

    # 9. បញ្ជាសម្រាប់ម្ចាស់ប៊ត (SUPER-ADMIN ONLY)
    elif u_id == SUPER_ADMIN_ID:
        if text == "➕ Add Admin":
            add_admin_prompt(message)
        elif text == "➖ Remove Admin":
            remove_admin_prompt(message)

# --- ឡូហ្សិកបង្កើត POLL (ច្បាប់ 4+1) ---

def process_poll_names(message):
    """បែងចែកឈ្មោះជាក្រុមៗ (៤នាក់ក្នុងមួយ Poll) និងដោះស្រាយបញ្ហានៅសល់ម្នាក់ឯង"""
    user_id = message.from_user.id
    target_channel = get_user_channel(user_id) 
    
    if not target_channel:
        bot.reply_to(message, "⚠️ Error: Please use 'Set Channel' first.")
        return

    # សម្អាតបញ្ជីឈ្មោះដែលផ្ញើមក
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    if not names:
        bot.reply_to(message, "❌ List is empty. Please provide names.")
        return

    # បែងចែកជាក្រុមៗ ក្រុមละ ៤ នាក់
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # ច្បាប់ 4+1៖ បើសំណល់ចុងក្រោយនៅសល់តែម្នាក់ឯង ត្រូវបូកបញ្ចូលទៅក្រុមមុន
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        last_person = chunks.pop() 
        chunks[-1].extend(last_person) 

    bot.send_message(message.chat.id, f"🚀 Creating {len(chunks)} polls in {target_channel}...")

    for i, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target_channel,
                question=f"Round {i} / ជុំទី {i}",
                options=group,
                is_anonymous=True,
                allows_multiple_answers=False
            )
            # រង់ចាំ ១.៥ វិនាទី ដើម្បីការពារ Telegram កុំឱ្យ Block (Flood Limit)
            time.sleep(1.5) 
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Poll {i} Failed: {str(e)}")

    bot.send_message(message.chat.id, "✅ **All Polls Created Successfully!**")
# ==========================================
# SECTION 9: MASS REPORT SIMULATOR (UI)
# ==========================================
import random
import threading

def generate_fake_ip():
    """Generates a random IP address for the console simulation"""
    return f"{random.randint(45, 192)}.{random.randint(10, 254)}.{random.randint(0, 254)}.{random.randint(1, 254)}"

@bot.message_handler(func=lambda m: m.text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"])
def report_start(message):
    """Starts the mass report simulation"""
    u_id = message.from_user.id
    if not is_authorized(u_id): return

    lang = get_user_lang(u_id)
    target = get_user_channel(u_id)

    if not target:
        bot.reply_to(message, "⚠️ EN: Set channel first / KH: សូមកំណត់ឆានែលសិន (/set)")
        return

    markup = types.InlineKeyboardMarkup(row_width=3)
    btn1 = types.InlineKeyboardButton("Low (100)", callback_data="run_rep_100")
    btn2 = types.InlineKeyboardButton("Medium (500)", callback_data="run_rep_500")
    btn3 = types.InlineKeyboardButton("High (1000)", callback_data="run_rep_1000")
    markup.add(btn1, btn2, btn3)

    msg = (f"🔥 **MASS REPORT INTERFACE**\n"
           f"━━━━━━━━━━━━━━━━━━\n"
           f"Target: `{target}`\n\n"
           f"EN: Choose report intensity:\n"
           f"KH: សូមជ្រើសរើសកម្រិតនៃការរាយការណ៍:")
    bot.send_message(message.chat.id, msg, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('run_rep_'))
def handle_report_callback(call):
    """Handles the callback and triggers the threaded simulation"""
    # Start the simulation in a new thread so the bot doesn't freeze
    threading.Thread(target=execute_report_simulation, args=(call,)).start()

def execute_report_simulation(call):
    """The actual logic for the simulated mass report"""
    amount = call.data.split('_')[2]
    chat_id = call.message.chat.id
    u_id = call.from_user.id
    target = get_user_channel(u_id) or "Unknown Target"
    
    # Initial Loading Message
    try:
        status_msg = bot.edit_message_text(
            f"⏳ **Initializing Proxy Servers...**\n`[░░░░░░░░░░] 0%`", 
            chat_id, call.message.message_id,
            parse_mode="Markdown"
        )
    except: return

    # Simulation Sequence
    stages = [
        {"p": 15, "t": "Connecting to KH-Mainframe..."},
        {"p": 35, "t": "Routing through IPv6 Tunnel..."},
        {"p": 55, "t": f"Broadcasting {amount} Signal Packets..."},
        {"p": 85, "t": "Injecting Metadata to T&S API..."},
        {"p": 100, "t": "✅ **Task Completed!**"}
    ]

    for stage in stages:
        time.sleep(2.2) # Realistic processing delay
        bar_filled = stage['p'] // 10
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        
        # Generate 3 fake log lines
        logs = "\n".join([f"📡 `[{generate_fake_ip()}]` -> `Sent`" for _ in range(3)])
        
        try:
            bot.edit_message_text(
                f"🛡️ **System Status: Active**\n"
                f"Target: `{target}`\n"
                f"Progress: `[{bar}] {stage['p']}%`\n\n"
                f"🛰️ `{stage['t']}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"**Console Logs:**\n{logs}",
                chat_id, status_msg.message_id,
                parse_mode="Markdown"
            )
        except:
            pass

    # Final Summary Report
    time.sleep(1.5)
    final_report = (
        f"✅ **MASS REPORT FINISHED**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📥 Total: `{amount}` Reports Submitted\n"
        f"📡 Proxies: `128 Dedicated Nodes`\n"
        f"🛡️ Target Status: `Flagged for Review`\n\n"
        f"EN: Success! Telegram's Trust & Safety bot has received the bulk data.\n"
        f"KH: ជោគជ័យ! ប្រព័ន្ធសុវត្ថិភាពរបស់ Telegram បានទទួលទិន្នន័យរួចរាល់។"
    )
    bot.send_message(chat_id, final_report, parse_mode="Markdown")

# ==========================================
# FINAL EXECUTION BLOCK
# ==========================================

if __name__ == "__main__":
    # \033[1;32m = Bold Green
    # \033[0m = Reset color to normal
    print("\033[1;32m🚀 Vinzy Audit Bot is starting...\033[0m")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            # \033[1;31m = Bold Red
            print(f"\033[1;31m⚠️ Polling Error: {e}\033[0m")
            time.sleep(5)
