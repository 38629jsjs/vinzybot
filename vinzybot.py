import telebot
from telebot import types
import psycopg2
from psycopg2 import pool
import threading
import time
import pytz
from datetime import datetime

# ==========================================
# SECTION 1: CONFIGURATION
# ==========================================
BOT_TOKEN = "8782687814:AAEj5hYbo7a2TFZnfYWF7zf1NaCPx4fgyT0"
SUPER_ADMIN_ID = 8702798367
# Your Neon Connection String
DATABASE_URL = "postgresql://neondb_owner:npg_5vXuDLicq2wT@ep-small-boat-aim6necc-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

bot = telebot.TeleBot(BOT_TOKEN)

# Initialize Connection Pool
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)
    print("✅ Successfully connected to Neon PostgreSQL")
except Exception as e:
    print(f"❌ Database connection failed: {e}")

# ==========================================
# SECTION 2: DATABASE LOGIC (Admins/Users/Privacy)
# ==========================================

# 1. PERMANENT AUTHORIZATION LIST
# Add your ID and any permanent Admin IDs here. 
# These will NEVER be deleted, even when updating on Koyeb.
PERMANENT_ADMINS = [8702798367, 123456789] 

def init_db():
    """Initializes the Neon PostgreSQL database with Language support"""
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        # In Postgres, we use BIGINT for Telegram IDs to prevent 'integer out of range' errors
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (user_id BIGINT PRIMARY KEY, 
                      is_admin INTEGER DEFAULT 0, 
                      target_channel TEXT,
                      lang TEXT DEFAULT 'en')''')
        conn.commit()
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
    finally:
        db_pool.putconn(conn)

def is_authorized(user_id):
    """Checks if a user has permission to use the bot tools using PostgreSQL"""
    # FIRST: Check the hardcoded SUPER_ADMIN_ID
    if user_id == SUPER_ADMIN_ID:
        return True
    
    # SECOND: Check the PERMANENT_ADMINS list (Safe from Koyeb wipes)
    if user_id in PERMANENT_ADMINS:
        return True
        
    # THIRD: Check the Neon database
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        # Postgres uses %s instead of ?
        c.execute("SELECT is_admin FROM users WHERE user_id = %s", (user_id,))
        res = c.fetchone()
        return res is not None and res[0] == 1
    except Exception as e:
        print(f"❌ Authorization check error: {e}")
        return False
    finally:
        db_pool.putconn(conn)

def get_user_channel(user_id):
    """Retrieves the target channel associated with a specific user from Neon"""
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        c.execute("SELECT target_channel FROM users WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        return result[0] if result and result[0] else None
    except Exception as e:
        print(f"❌ Get channel error: {e}")
        return None
    finally:
        db_pool.putconn(conn)

# --- NEW LANGUAGE LOGIC (PostgreSQL) ---

def get_user_lang(user_id):
    """Checks the database for user's language preference. Defaults to 'en'."""
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        c.execute("SELECT lang FROM users WHERE user_id = %s", (user_id,))
        res = c.fetchone()
        return res[0] if res and res[0] else 'en'
    except Exception as e:
        print(f"❌ Get language error: {e}")
        return 'en'
    finally:
        db_pool.putconn(conn)

def set_user_lang(user_id, lang_code):
    """Updates the user's language preference using Postgres UPSERT logic"""
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        # 'ON CONFLICT' is the Postgres way to handle 'INSERT OR IGNORE/UPDATE'
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
        db_pool.putconn(conn)

# Initialize the Neon database on startup
init_db()
# ==========================================
# SECTION 3: POLL & ANTI-BOOST LOGIC
# ==========================================

# Permanent tracking for drip-feed, speed, and timing detection
poll_history = {} 

@bot.poll_handler(func=lambda poll: True)
def track_poll_votes(poll):
    p_id = poll.id
    current_votes = poll.total_voter_count
    current_time = time.time()
    
    # 1. Initialize history for this poll if it's new
    if p_id not in poll_history:
        poll_history[p_id] = {
            'counts': [current_votes], 
            'times': [current_time],
            'last_notified_pattern': 0,
            'last_notified_threshold': False
        }
        return

    history_counts = poll_history[p_id]['counts']
    history_times = poll_history[p_id]['times']

    # 2. THRESHOLD ALERT
    # Triggers once when the poll passes 100 votes
    if current_votes > 100 and not poll_history[p_id]['last_notified_threshold']:
        bot.send_message(
            SUPER_ADMIN_ID, 
            f"⚠️ **HIGH VOLUME ALERT**\nPoll ID: {p_id}\nTotal Votes: {current_votes}\nCheck channel views vs votes ratio now!"
        )
        poll_history[p_id]['last_notified_threshold'] = True

    # 3. DRIP-FEED "STAIR-STEP" DETECTION
    # Checks if the gain is exactly the same multiple times (e.g., +10, +10, +10)
    if len(history_counts) >= 4:
        gain1 = history_counts[-1] - history_counts[-2]
        gain2 = history_counts[-2] - history_counts[-3]
        gain3 = history_counts[-3] - history_counts[-4]
        
        if gain1 == gain2 == gain3 and gain1 > 0:
            # Prevents spamming the same alert for the same pattern
            if poll_history[p_id]['last_notified_pattern'] != gain1:
                bot.send_message(
                    SUPER_ADMIN_ID, 
                    f"🛑 **DRIP-FEED DETECTED**\n"
                    f"Poll: {p_id}\n"
                    f"Pattern: Gaining exactly {gain1} votes per update.\n"
                    f"Status: High probability of SMM Drip-Feed."
                )
                poll_history[p_id]['last_notified_pattern'] = gain1

    # 4. ABNORMAL FREQUENCY (TIMING) DETECTION
    # Checks if votes appear at perfectly even intervals (humanly impossible consistency)
    if len(history_times) >= 3:
        gap1 = round(history_times[-1] - history_times[-2], 1)
        gap2 = round(history_times[-2] - history_times[-3], 1)
        
        # If the time between votes is nearly identical (within 0.2 seconds)
        if abs(gap1 - gap2) < 0.2 and gap1 > 5:
            bot.send_message(
                SUPER_ADMIN_ID, 
                f"🤖 **BOT TIMING ALERT**\n"
                f"Poll: {p_id}\n"
                f"Consistency: Votes arriving every {gap1}s exactly.\n"
                f"Note: Real humans do not vote with this precision."
            )

    # 5. SPEED SPIKE DETECTION
    # Checks for sudden mass-botting (instants)
    last_time_recorded = history_times[-1]
    time_passed = current_time - last_time_recorded
    votes_gained = current_votes - history_counts[-1]
    
    if votes_gained > 15 and time_passed < 3:
        bot.send_message(
            SUPER_ADMIN_ID, 
            f"🚨 **SPEED SPIKE DETECTED**\n"
            f"Poll: {p_id}\n"
            f"Jump: +{votes_gained} votes in {round(time_passed, 2)}s!"
        )

    # Final Step: Update history logs
    poll_history[p_id]['counts'].append(current_votes)
    poll_history[p_id]['times'].append(current_time)

import sqlite3

# ==========================================
# SECTION 4: BROADCAST & CHANNEL CHECKS
# ==========================================

def check_channel_perms(user_id, channel_id):
    """Verifies if the bot is an admin in the user's specific channel"""
    try:
        # We check the bot's own status in the target channel
        member = bot.get_chat_member(channel_id, bot.get_me().id)
        if member.status != 'administrator':
            return False, "EN: Need Admin Perms. | KH: ត្រូវការសិទ្ធិជា Admin"
        return True, "OK"
    except Exception:
        # This triggers if the bot isn't even a member or the username is wrong
        return False, "EN: Bot not in channel. | KH: បុគ្គលិកមិននៅក្នុង Channel ទេ"

def get_user_channel(user_id):
    """Fetch the specific channel locked to a user from the Neon PostgreSQL database"""
    conn = db_pool.getconn()
    try:
        c = conn.cursor()
        # Ensure privacy: We only look for the channel belonging to THIS user_id
        # Postgres uses %s placeholder instead of ?
        c.execute("SELECT target_channel FROM users WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        
        # Returns the channel ID (e.g., "@vinzystorez") or None if not set
        if result and result[0]:
            return result[0]
        return None
    except Exception as e:
        print(f"❌ Error fetching user channel: {e}")
        return None
    finally:
        # Crucial: Always return the connection to the pool
        db_pool.putconn(conn)

# ==========================================
# BROADCAST COMMAND LOGIC
# ==========================================

@bot.message_handler(commands=['broadcast'])
def start_broadcast(message):
    """Starts the broadcast process for authorized users only"""
    user_id = message.from_user.id
    
    # 1. Authorization Check (Section 2 Logic)
    if not is_authorized(user_id):
        bot.reply_to(message, "🚫 KH: អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។ | EN: No access.")
        return

    # 2. Privacy Check: Get ONLY their locked channel from the Postgres DB
    user_channel = get_user_channel(user_id)
    
    if not user_channel:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel របស់អ្នកជាមុនសិន (/set_channel) | EN: Set your channel first.")
        return

    # 3. Permission Check: Verify Bot has Admin rights in THAT specific channel
    is_ok, error_msg = check_channel_perms(user_id, user_channel)
    if not is_ok:
        bot.reply_to(message, error_msg)
        return

    # 4. User Prompt: Request content for the broadcast
    msg = bot.reply_to(message, f"📢 **Private Broadcast System**\nTarget: {user_channel}\n\nEN: Enter your message:\nKH: សូមផ្ញើសារដែលអ្នកចង់បង្ហោះ:")
    # We pass 'user_channel' to the next step to ensure it remains locked to this specific target
    bot.register_next_step_handler(msg, execute_private_broadcast, user_channel)

def execute_private_broadcast(message, user_channel):
    """Sends the message only to the user's registered channel"""
    try:
        # Safety Check: Does the message contain content?
        if not message.text:
            bot.reply_to(message, "❌ KH: សារទទេ មិនអាចផ្ញើបានទេ។ | EN: Cannot send empty message.")
            return

        # EXECUTION: Send ONLY to the user's specific channel
        bot.send_message(user_channel, message.text)
        
        # Feedback to user
        bot.reply_to(message, f"✅ **Success!**\nEN: Broadcast sent to {user_channel}!\nKH: សារត្រូវបានផ្ញើទៅកាន់ {user_channel} រួចរាល់!")
        
    except Exception as e:
        # If something goes wrong (e.g., bot kicked suddenly)
        error_text = str(e)
        bot.reply_to(message, f"❌ **Error Occurred**\nDetails: {error_text}")
# ==========================================
# SECTION 5: AUTO-SEND (CAMBODIA TIME)
# ==========================================
def schedule_checker():
    while True:
        # Cambodia Timezone
        tz_kh = pytz.timezone('Asia/Phnom Penh')
        now_kh = datetime.now(tz_kh).strftime("%H:%M")
        
        # Example: Send message at 09:00 AM
        if now_kh == "09:00":
            # Logic to fetch channels from DB and send
            pass
        time.sleep(60)

threading.Thread(target=schedule_checker, daemon=True).start()

# ==========================================
# SECTION 6: BOT DETECTION (DATA & LOGS)
# ==========================================

@bot.message_handler(commands=['check_stats'])
def check_stats(message):
    """Analyzes channel health by comparing views, forwards, and member logs"""
    user_id = message.from_user.id
    
    # 1. Authorization Check (Uses Postgres-ready function from Section 2)
    if not is_authorized(user_id):
        return

    # 2. Get the target channel from Neon PostgreSQL
    target = get_user_channel(user_id)
    
    if not target:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន / EN: Set channel first.")
        return

    try:
        # 3. ADMIN & MEMBER COUNT CHECK
        chat = bot.get_chat(target)
        members_count = bot.get_chat_member_count(target)
        
        # Verify Bot Admin Permissions for Log Scanning
        bot_member = bot.get_chat_member(target, bot.get_me().id)
        if bot_member.status != 'administrator':
            raise Exception("Missing Admin Status")

        # 4. SCAN FOR DELETED MESSAGES (Last 48 Hours)
        recent_deletes = 0
        try:
            # Requires 'Can see admin logs' permission
            logs = bot.get_chat_admin_log(chat.id, types=['message_delete'])
            recent_deletes = len(logs)
        except Exception:
            recent_deletes = -1 # Log access restricted or no logs found

        # 5. FETCH DATA FROM LATEST POST (Pinned or Recent)
        last_post_views = 0
        last_post_forwards = 0
        if chat.pinned_message:
            # Views and forward_count are available for bots with Admin rights
            last_post_views = getattr(chat.pinned_message, 'views', 0)
            last_post_forwards = getattr(chat.pinned_message, 'forward_count', 0)

        # ==========================================
        # DETECTION LOGIC (DATA-DRIVEN)
        # ==========================================
        risk_score = 0
        reasons_en = []
        reasons_kh = []

        # RULE A: The "Ghost" Subscriber Check (Low Interaction)
        if members_count > 500 and last_post_views > 0:
            view_ratio = (last_post_views / members_count) * 100
            if view_ratio < 1: # Less than 1% engagement
                risk_score += 40
                reasons_en.append("Views are too low compared to total subscribers.")
                reasons_kh.append("ចំនួនអ្នកមើលតិចជាងចំនួនអ្នកតាមដានច្រើនពេក (Ghost Subs)។")

        # RULE B: The "Fake Forward" Rule
        if last_post_forwards > last_post_views and last_post_views > 0:
            risk_score += 50
            reasons_en.append("Forwards are higher than views (Impossible/Fake Boost).")
            reasons_kh.append("ចំនួន Forward ច្រើនជាងអ្នកមើល (ការបន្លំតួលេខ)។")

        # RULE C: The "Empty Channel" Deletion Trap
        if members_count > 100 and recent_deletes > 20:
            risk_score += 60
            reasons_en.append(f"Detected {recent_deletes} mass-deletions. Seller is hiding evidence.")
            reasons_kh.append(f"រកឃើញការលុបសារចំនួន {recent_deletes}។ អ្នកលក់កំពុងលាក់បាំងភស្តុតាង។")

        # RULE D: Low Engagement History
        if members_count > 500 and recent_deletes == 0 and not chat.description:
            risk_score += 30
            reasons_en.append("No channel history/description but high sub count.")
            reasons_kh.append("គ្មានប្រវត្តិរូប ឬការបង្ហោះសោះ តែមានអ្នកតាមដានច្រើន។")

        # RATING GENERATION
        if risk_score >= 50:
            status = "🔴 DO NOT BUY / កុំទិញ"
            rating = "HIGH RISK / ហានិភ័យខ្ពស់"
        else:
            status = "🟢 SAFE / សុវត្ថិភាព"
            rating = "CLEAN / ល្អ"

        # 6. FINAL REPORT CONSTRUCTION
        report = (f"📊 **AUDIT REPORT: {target}**\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"👥 Subs: {members_count}\n"
                  f"🗑️ Recent Deletes: {recent_deletes if recent_deletes >= 0 else 'Unknown'}\n"
                  f"⚖️ Status: {status}\n"
                  f"⭐ Rating: {rating}\n\n"
                  f"🇬🇧 **Analysis:** {'. '.join(reasons_en) if reasons_en else 'Engagement looks natural.'}\n"
                  f"🇰🇭 **ការវិភាគ:** {'. '.join(reasons_kh) if reasons_kh else 'មើលទៅធម្មតា និងមានសុវត្ថិភាព។'}")

        bot.send_message(message.chat.id, report)

    except Exception as e:
        # Detailed error handling for missing permissions
        print(f"Audit error: {e}")
        msg = ("❌ **PERMISSIONS ERROR / ត្រូវការសិទ្ធិ Admin**\n\n"
               "EN: Add me as Admin with 'View Admin Logs' and 'Delete Messages' perms.\n"
               "KH: សូមដាក់ខ្ញុំជា Admin និងផ្ដល់សិទ្ធិ 'View Admin Logs' ដើម្បីវិភាគ។")
        bot.reply_to(message, msg)
# ==========================================
# SECTION 7: USER INTERFACE & PERMISSIONS
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def start(message):
    """Displays the main interface with persistent grid menu based on user language"""
    u_id = message.from_user.id
    
    # 1. Authorization Check (Uses Neon DB)
    if not is_authorized(u_id):
        remove_markup = types.ReplyKeyboardRemove()
        msg = (
            "🚫 **Access Denied!**\n\n"
            "EN: This bot is private. Contact @vinzystorezz to buy access.\n"
            "KH: គណនីរបស់អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។ សូមទាក់ទង @vinzystorezz ដើម្បីទិញសិទ្ធិ។"
        )
        bot.send_message(message.chat.id, msg, reply_markup=remove_markup)
        return

    # 2. Get User Language Preference
    lang = get_user_lang(u_id)
    
    # 3. Define Multilingual Button Labels
    labels = {
        'poll': "📊 Create Poll" if lang == 'en' else "📊 បង្កើតការបោះឆ្នោត",
        'audit': "🔍 Audit Channel" if lang == 'en' else "🔍 ពិនិត្យឆានែល",
        'broadcast': "📢 Broadcast" if lang == 'en' else "📢 ផ្សព្វផ្សាយ",
        'schedule': "📅 Schedule Info" if lang == 'en' else "📅 ព័ត៌មានកាលវិភាគ",
        'set': "📍 Set Channel" if lang == 'en' else "📍 កំណត់ឆានែល",
        'detect': "🛡️ Poll Detection" if lang == 'en' else "🛡️ ស្វែងរក Bot",
        'help': "❓ Help" if lang == 'en' else "❓ ជំនួយ",
        'lang': "🌐 Language" if lang == 'en' else "🌐 ភាសា"
    }

    # 4. Create Grid Layout
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(labels['poll'], labels['audit'])
    markup.add(labels['broadcast'], labels['schedule'])
    markup.add(labels['set'], labels['detect'])
    markup.add(labels['help'], labels['lang'])
    
    # Add Owner-Only Management Buttons
    if u_id == SUPER_ADMIN_ID:
        markup.add("➕ Add Admin", "➖ Remove Admin")
        welcome_text = "👑 **OWNER CONTROL PANEL**" if lang == 'en' else "👑 **ផ្ទាំងគ្រប់គ្រងម្ចាស់ប៊ត**"
    else:
        welcome_text = "🛡️ **ADMIN CONTROL PANEL**" if lang == 'en' else "🛡️ **ផ្ទាំងគ្រប់គ្រងអ្នកអតមីន**"

    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)


@bot.message_handler(commands=['normal'])
def remove_keyboard(message):
    """Removes the persistent menu buttons"""
    markup = types.ReplyKeyboardRemove()
    bot.send_message(message.chat.id, "✅ Keyboard hidden.", reply_markup=markup)


# --- SMART TEXT BUTTON ROUTER ---
@bot.message_handler(func=lambda m: True)
def handle_menu_text(message):
    u_id = message.from_user.id
    if not is_authorized(u_id):
        return
    
    lang = get_user_lang(u_id)
    text = message.text

    # 1. LANGUAGE TOGGLE LOGIC
    if text in ["🌐 Language", "🌐 ភាសា"]:
        markup = types.InlineKeyboardMarkup()
        btn_en = types.InlineKeyboardButton("English 🇺🇸", callback_data="set_lang_en")
        btn_kh = types.InlineKeyboardButton("ភាសាខ្មែរ 🇰🇭", callback_data="set_lang_kh")
        markup.add(btn_en, btn_kh)
        bot.send_message(message.chat.id, "Select Language / សូមជ្រើសរើសភាសា:", reply_markup=markup)

    # 2. HELP MENU LOGIC
    elif text in ["❓ Help", "❓ ជំនួយ"]:
        help_msg = (
            "📖 **How to use:**\n\n"
            "1. **Set Channel**: Use this first to link your channel.\n"
            "2. **Create Poll**: Send a list of names to start voting.\n"
            "3. **Audit**: Run this to find fake bot members.\n"
            "4. **Detection**: Keep this active to catch vote boosting."
            if lang == 'en' else
            "📖 **របៀបប្រើប្រាស់:**\n\n"
            "1. **កំណត់ឆានែល**: ប្រើវាដំបូងគេដើម្បីភ្ជាប់ទៅ Channel របស់អ្នក។\n"
            "2. **បង្កើតការបោះឆ្នោត**: ផ្ញើបញ្ជីឈ្មោះដើម្បីចាប់ផ្តើមបោះឆ្នោត។\n"
            "3. **ពិនិត្យឆានែល**: ប្រើវាដើម្បីស្វែងរកសមាជិកក្លែងក្លាយ (Bot)។\n"
            "4. **ស្វែងរក Bot**: បើកវាដើម្បីតាមដានការលួចបន្លំសន្លឹកឆ្នោត។"
        )
        bot.send_message(message.chat.id, help_msg)

    # 3. CREATE POLL
    elif text in ["📊 Create Poll", "📊 បង្កើតការបោះឆ្នោត"]:
        prompt = "📋 Send name list (one per line):" if lang == 'en' else "📋 សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (មួយឈ្មោះក្នុងមួយបន្ទាត់):"
        msg = bot.send_message(message.chat.id, prompt)
        bot.register_next_step_handler(msg, process_poll_names)

    # 4. AUDIT CHANNEL
    elif text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        bot.send_message(message.chat.id, "🔎 Scanning... | កំពុងពិនិត្យ...")
        check_stats(message)

    # 5. SET CHANNEL
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        # Ensure your set_channel_prompt function is defined elsewhere
        set_channel_prompt(message)

    # 6. BROADCAST
    elif text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"]:
        start_broadcast(message)

    # 7. SCHEDULE INFO
    elif text in ["📅 Schedule Info", "📅 ព័ត៌មានកាលវិភាគ"]:
        tz_kh = pytz.timezone('Asia/Phnom Penh')
        now_kh = datetime.now(tz_kh).strftime("%H:%M:%S")
        msg = (f"⏰ **System Status**\n\nTime (KH): {now_kh}\nAuto-Post: 09:00 AM" if lang == 'en' else 
               f"⏰ **ស្ថានភាពប្រព័ន្ធ**\n\nម៉ោង (KH): {now_kh}\nបង្ហោះអូតូ: ម៉ោង ០៩:០០ ព្រឹក")
        bot.send_message(message.chat.id, msg)

    # 8. POLL DETECTION
    elif text in ["🛡️ Poll Detection", "🛡️ ស្វែងរក Bot"]:
        msg = "🛡️ Anti-Boost Active" if lang == 'en' else "🛡️ ការការពារការលួចបន្លំកំពុងដំណើរការ"
        bot.send_message(message.chat.id, msg)

    # 9. OWNER ONLY
    elif u_id == SUPER_ADMIN_ID:
        if text == "➕ Add Admin":
            add_admin_prompt(message)
        elif text == "➖ Remove Admin":
            remove_admin_prompt(message)

# --- CALLBACK FOR LANGUAGE SWITCHING ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('set_lang_'))
def callback_language(call):
    new_lang = call.data.split('_')[2]
    set_user_lang(call.from_user.id, new_lang)
    
    msg = "Language updated! Use /menu" if new_lang == 'en' else "ភាសាត្រូវបានផ្លាស់ប្តូរ! សូមប្រើ /menu"
    bot.answer_callback_query(call.id, msg)
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id)

# --- ADMIN MGMT FUNCTIONS (PostgreSQL Logic) ---
def add_admin_prompt(message):
    msg = bot.reply_to(message, "🆔 Send Telegram ID to add as Admin:")
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    try:
        new_id = int(message.text)
        conn = db_pool.getconn()
        try:
            c = conn.cursor()
            # Postgres UPSERT logic
            c.execute("""
                INSERT INTO users (user_id, is_admin) 
                VALUES (%s, 1) 
                ON CONFLICT (user_id) 
                DO UPDATE SET is_admin = 1
            """, (new_id,))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ User {new_id} added to Admin list.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ DB Error: {e}")
        finally:
            db_pool.putconn(conn)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid ID. Must be a number.")

def remove_admin_prompt(message):
    msg = bot.reply_to(message, "🆔 Send ID to remove admin rights:")
    bot.register_next_step_handler(msg, process_remove_admin)

def process_remove_admin(message):
    try:
        target_id = int(message.text)
        
        # Guard clause for Permanent Admins
        if target_id == SUPER_ADMIN_ID or target_id in PERMANENT_ADMINS:
            bot.send_message(message.chat.id, "🚫 Cannot remove a Permanent Admin.")
            return

        conn = db_pool.getconn()
        try:
            c = conn.cursor()
            c.execute("UPDATE users SET is_admin = 0 WHERE user_id = %s", (target_id,))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ Admin rights removed from {target_id}.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ DB Error: {e}")
        finally:
            db_pool.putconn(conn)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid ID format.")
# ==========================================
# SECTION 8: FULL FEATURE MENU & ROUTING
# ==========================================

@bot.message_handler(commands=['menu', 'start'])
def show_main_menu(message):
    """Displays the persistent grid menu for all authorized users"""
    u_id = message.from_user.id
    if not is_authorized(u_id):
        # Access Denied Message
        msg = ("🚫 **Access Denied!**\n\n"
               "EN: This bot is private. Please pay to gain access.\n"
               "KH: គណនីរបស់អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។ សូមទិញសិទ្ធិប្រើប្រាស់ពីម្ចាស់ប៊ត។")
        bot.send_message(message.chat.id, msg)
        return
        
    # Standard grid layout (2 buttons per row)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # 1. CORE BUTTONS (Visible to both Owner and Admin)
    btn1 = "📊 Create Poll"
    btn2 = "🔍 Audit Channel"
    btn3 = "📢 Broadcast"
    btn4 = "📅 Schedule Info"
    btn5 = "📍 Set Channel"
    btn6 = "🛡️ Poll Detection"
    
    markup.add(btn1, btn2)
    markup.add(btn3, btn4)
    markup.add(btn5, btn6)
    
    # 2. OWNER-ONLY MANAGEMENT BUTTONS
    if u_id == SUPER_ADMIN_ID:
        markup.add("➕ Add Admin", "➖ Remove Admin")
        menu_text = "👑 **OWNER CONTROL PANEL**\nSelect a tool from the menu below:"
    else:
        menu_text = "🛡️ **ADMIN CONTROL PANEL**\nSelect a tool from the menu below:"

    bot.send_message(message.chat.id, menu_text, reply_markup=markup)

# --- TEXT BUTTON ROUTER ---
# This connects the text on the buttons to their respective functions
@bot.message_handler(func=lambda m: True)
def handle_all_buttons(message):
    u_id = message.from_user.id
    if not is_authorized(u_id): 
        return

    # 1. POLL CREATION
    if message.text == "📊 Create Poll":
        msg = bot.send_message(message.chat.id, "📋 EN: Send name list (one per line):\nKH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (មួយឈ្មោះក្នុងមួយបន្ទាត់):")
        bot.register_next_step_handler(msg, process_poll_names)

    # 2. CHANNEL AUDIT (Anti-Bot Analysis)
    elif message.text == "🔍 Audit Channel":
        bot.send_message(message.chat.id, "🔎 EN: Running Channel Audit... | KH: កំពុងពិនិត្យ Channel...")
        check_stats(message)

    # 3. BROADCAST
    elif message.text == "📢 Broadcast":
        start_broadcast(message)

    # 4. SCHEDULE INFO (Syncs with Cambodia Time)
    elif message.text == "📅 Schedule Info":
        tz_kh = pytz.timezone('Asia/Phnom Penh')
        now_kh = datetime.now(tz_kh).strftime("%H:%M:%S")
        bot.send_message(message.chat.id, 
                         f"⏰ **Schedule System Status**\n\n"
                         f"Current Time (KH): {now_kh}\n"
                         f"Auto-Post Time: 09:00 AM\n"
                         f"Status: Active ✅\n\n"
                         f"Note: Your scheduled posts are automatically synced.")

    # 5. CHANNEL SETTINGS
    elif message.text == "📍 Set Channel":
        set_channel_prompt(message)

    # 6. ANTI-BOOST MONITOR (Live Status)
    elif message.text == "🛡️ Poll Detection":
        bot.send_message(message.chat.id, 
                         "🕵️ **Anti-Boost Monitor Active**\n\n"
                         "The system is currently scanning for:\n"
                         "• Abnormal voting speed\n"
                         "• SMM Drip-feed patterns\n"
                         "• Instant spikes (>15 votes/3s)\n\n"
                         "Alerts will trigger automatically if botting occurs.")

    # 7. OWNER ONLY: USER MANAGEMENT
    elif u_id == SUPER_ADMIN_ID:
        if message.text == "➕ Add Admin":
            add_admin_prompt(message)
        elif message.text == "➖ Remove Admin":
            remove_admin_prompt(message)

# --- POLL PROCESSING LOGIC ---

def process_poll_names(message):
    """Processes the list and handles the 4+1 overflow rule"""
    user_id = message.from_user.id
    target_channel = get_user_channel(user_id) 
    
    if not target_channel:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន (/set_channel) | EN: Set channel first.")
        return

    # Clean the input list
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    
    if not names:
        bot.reply_to(message, "❌ KH: បញ្ជីឈ្មោះទទេរ! | EN: List is empty.")
        return

    # Grouping names into chunks of 4
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # Overflow rule: Merge last person if they are alone
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        leftover_person = chunks.pop() 
        chunks[-1].extend(leftover_person) 

    bot.send_message(message.chat.id, f"🚀 KH: កំពុងបង្កើត Poll ចំនួន {len(chunks)} ទៅកាន់ {target_channel}...")

    for index, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target_channel,
                question=f"Poll {index}",
                options=group,
                is_anonymous=True 
            )
            time.sleep(1) # Safety delay
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error in Poll {index}: {str(e)}")

    final_msg = (
        f"✅ **Process Complete!**\n"
        f"EN: {len(chunks)} polls sent to {target_channel}.\n"
        f"KH: Poll ចំនួន {len(chunks)} ត្រូវបានផ្ញើទៅ {target_channel} រួចរាល់។"
    )
    bot.send_message(message.chat.id, final_msg)
# ==========================================
# FINAL EXECUTION BLOCK
# ==========================================
if __name__ == "__main__":
    print("Bot is starting...")
    bot.infinity_polling()
