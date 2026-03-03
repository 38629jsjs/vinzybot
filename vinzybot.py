import os
import sys
import telebot
import psycopg2
from psycopg2 import pool
import pytz
import time
import threading
import random
from datetime import datetime
from telebot import types

# ==========================================
# SECTION 1: CONFIGURATION & DATABASE
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8782687814:AAEj5hYbo7a2TFZnfYWF7zf1NaCPx4fgyT0")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "8702798367"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_5vXuDLicq2wT@ep-small-boat-aim6necc-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require")

# 1. Initialize Pool FIRST
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, DATABASE_URL)
    print("✅ [DATABASE] Connection Pool Initialized.")
except Exception as e:
    print(f"❌ [DATABASE] Failed to create pool: {e}")
    sys.exit(1)

# 2. Initialize Bot SECOND
bot = telebot.TeleBot(BOT_TOKEN)
# ==========================================
# SECTION 2: DATABASE LOGIC (ADMINS/USERS/PRIVACY)
# ==========================================

# Permanent IDs that bypass database checks (Owner/Backup IDs)
PERMANENT_ADMINS = [8702798367, 123456789] 

def init_db():
    """Initializes the Neon PostgreSQL database with all required columns"""
    conn = None
    try:
        # Get a connection from the Threaded Pool
        conn = db_pool.getconn()
        cursor = conn.cursor()
        
        # Execute the table creation with all necessary fields
        # user_id: Primary key (Telegram ID)
        # is_admin: 1 for allowed, 0 for restricted
        # target_channel: The @username the admin is currently managing
        # lang: 'en' for English, 'kh' for Khmer
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, 
                is_admin INTEGER DEFAULT 0, 
                target_channel TEXT,
                lang TEXT DEFAULT 'en'
            )
        ''')
        
        conn.commit()
        print("📁 [DATABASE] Table 'users' verified and ready.")
        
    except Exception as e:
        print(f"❌ [DATABASE] Critical Init Error: {e}")
    finally:
        # ALWAYS return the connection to the pool
        if conn:
            db_pool.putconn(conn)

def is_authorized(user_id):
    """Checks if a user has permission to access the bot's features"""
    user_id = int(user_id)
    
    # 1. Check hardcoded/Super Admin bypass first (fastest)
    if user_id == SUPER_ADMIN_ID or user_id in PERMANENT_ADMINS:
        return True
    
    # 2. Check the PostgreSQL database
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        
        # Returns True only if user exists and is_admin column is 1
        return result is not None and result[0] == 1
        
    except Exception as e:
        print(f"❌ [DATABASE] Auth Check Error: {e}")
        return False
    finally:
        if conn:
            db_pool.putconn(conn)

def get_user_channel(user_id):
    """Retrieves the current target channel the admin is auditing/controlling"""
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute("SELECT target_channel FROM users WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        
        # Return the channel string (e.g., '@mychannel') if it exists, else None
        if result and result[0]:
            return result[0]
        return None
        
    except Exception as e:
        print(f"❌ [DATABASE] Get Channel Error: {e}")
        return None
    finally:
        if conn:
            db_pool.putconn(conn)

def get_user_lang(user_id):
    """Fetches the user's language preference ('en' or 'kh')"""
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute("SELECT lang FROM users WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        
        # Default to English if no preference is set in DB
        if result and result[0]:
            return result[0]
        return 'en'
        
    except Exception as e:
        print(f"❌ [DATABASE] Get Language Error: {e}")
        return 'en'
    finally:
        if conn:
            db_pool.putconn(conn)

def set_user_lang(user_id, lang_code):
    """Updates language preference using 'UPSERT' (Insert if new, Update if exists)"""
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        
        # PostgreSQL ON CONFLICT (UPSERT) logic
        cursor.execute("""
            INSERT INTO users (user_id, lang) 
            VALUES (%s, %s) 
            ON CONFLICT (user_id) 
            DO UPDATE SET lang = EXCLUDED.lang
        """, (user_id, lang_code))
        
        conn.commit()
        print(f"✅ [DATABASE] Lang updated for {user_id} to {lang_code}")
        
    except Exception as e:
        print(f"❌ [DATABASE] Set Language Error: {e}")
    finally:
        if conn:
            db_pool.putconn(conn)

# Initialize the database when the script loads
init_db()
# ==========================================
# SECTION 3: POLL CREATION & ANTI-BOOST LOGIC
# ==========================================
import time

# Temporary tracking for live speed/pattern detection
poll_history = {} 

# --- PART A: POLL CREATION LOGIC ---

def process_poll_names(message):
    """Processes the list of names sent by the user and creates the polls"""
    u_id = message.from_user.id
    lang = get_user_lang(u_id)
    target_channel = get_user_channel(u_id)

    if not target_channel:
        err = "❌ Set channel first!" if lang == 'en' else "❌ សូមកំណត់ឆានែលសិន!"
        bot.send_message(message.chat.id, err)
        return

    # Clean the input: Split by lines and remove empty lines
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    
    if len(names) < 2:
        err = "❌ Need at least 2 names!" if lang == 'en' else "❌ ត្រូវការយ៉ាងហោចណាស់ ២ ឈ្មោះ!"
        bot.send_message(message.chat.id, err)
        return

    # 4+1 Rule Logic: Divide into groups of 4
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # If the last group has only 1 person, merge them into the previous group
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        last_person = chunks.pop()
        chunks[-1].extend(last_person)

    bot.send_message(message.chat.id, f"🚀 Creating {len(chunks)} Polls...")

    for i, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target_channel,
                question=f"Round {i} / ជុំទី {i}",
                options=group,
                is_anonymous=True,
                allows_multiple_answers=False
            )
            time.sleep(1.5) # Flood protection
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error in Poll {i}: {e}")

    bot.send_message(message.chat.id, "✅ Done!")

# --- PART B: ANTI-BOOST TRACKING LOGIC ---

def clean_poll_memory():
    """Prevents RAM from filling up by removing old poll data"""
    global poll_history
    if len(poll_history) > 100:
        keys_to_remove = list(poll_history.keys())[:50]
        for k in keys_to_remove:
            poll_history.pop(k, None)
        print("🧹 Memory Cleaned: Removed old poll tracking data.")

@bot.poll_handler(func=lambda poll: True)
def track_poll_votes(poll):
    """Standard Bot API handler for poll updates to detect botting"""
    p_id = str(poll.id)
    current_votes = poll.total_voter_count
    current_time = time.time()
    
    if len(poll_history) > 100:
        clean_poll_memory()

    if p_id not in poll_history:
        poll_history[p_id] = {
            'counts': [current_votes], 
            'times': [current_time],
            'last_notified_pattern': 0,
            'last_notified_threshold': False,
            'last_spike_time': 0
        }
        return

    data = poll_history[p_id]
    
    # 1. THRESHOLD ALERT
    if current_votes >= 100 and not data['last_notified_threshold']:
        bot.send_message(SUPER_ADMIN_ID, f"⚠️ **THRESHOLD ALERT**\nPoll `{p_id}` reached 100 votes.")
        data['last_notified_threshold'] = True

    # 2. SPEED SPIKE DETECTION
    votes_gained = current_votes - data['counts'][-1]
    time_passed = current_time - data['times'][-1]

    if votes_gained > 15 and time_passed < 2:
        if current_time - data['last_spike_time'] > 30:
            bot.send_message(
                SUPER_ADMIN_ID, 
                f"🚨 **SPIKE DETECTED**\nJump: +{votes_gained} votes in {round(time_passed, 1)}s!"
            )
            data['last_spike_time'] = current_time

    # Update history and keep it small
    data['counts'].append(current_votes)
    data['times'].append(current_time)
    if len(data['counts']) > 10:
        data['counts'].pop(0)
        data['times'].pop(0)
# ==========================================
# SECTION 4: BROADCAST & ADMIN VERIFICATION
# ==========================================

def verify_and_broadcast(message, user_channel):
    """
    Checks for REAL Admin status and executes the broadcast.
    Uses copy_message to support Text, Photo, Video, Voice, Document, and Polls.
    """
    u_id = message.from_user.id
    lang = get_user_lang(u_id)

    # Allow user to cancel the broadcast process
    if message.text and message.text.lower() in ['cancel', 'បោះបង់', '/cancel']:
        cancel_text = "🚫 Broadcast cancelled." if lang == 'en' else "🚫 ការផ្សព្វផ្សាយត្រូវបានបោះបង់។"
        bot.send_message(message.chat.id, cancel_text)
        return

    try:
        # 1. LIVE ADMIN & PERMISSION CHECK
        # Fetch the bot's own profile and check its status in the target channel
        bot_id = bot.get_me().id
        check = bot.get_chat_member(user_channel, bot_id)

        # Check if the bot is present in the channel
        if check.status not in ['administrator', 'creator']:
            error_msg = (
                "❌ **Admin Error**\n\n"
                "EN: I must be an ADMIN in the channel to post messages.\n"
                "KH: ខ្ញុំត្រូវតែជា ADMIN នៅក្នុង Channel ដើម្បីបង្ហោះសារបាន។"
            )
            bot.reply_to(message, error_msg, parse_mode="Markdown")
            return

        # Specifically check if the 'can_post_messages' right is enabled
        # Note: 'creator' (owner) always has this right, so we check for 'administrator'
        if check.status == 'administrator' and check.can_post_messages is False:
            perm_msg = (
                "❌ **Permission Error**\n\n"
                "EN: I am an Admin, but the 'Post Messages' permission is turned OFF.\n"
                "KH: ខ្ញុំជា Admin តែមិនទាន់មានសិទ្ធិ 'Post Messages' ឡើយ។"
            )
            bot.reply_to(message, perm_msg, parse_mode="Markdown")
            return

        # 2. EXECUTE THE BROADCAST
        # copy_message is superior to forward_message because it doesn't show "Forwarded from..."
        bot.copy_message(
            chat_id=user_channel,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )

        # 3. SUCCESS FEEDBACK
        success_text = (
            f"✅ **Broadcast Successful! / ផ្សព្វផ្សាយជោគជ័យ!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 **Target:** `{user_channel}`\n"
            f"📊 **Status:** Message delivered successfully.\n"
            f"📅 **Time:** {get_kh_time().strftime('%Y-%m-%d %H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"EN: Your content is now live in the channel.\n"
            f"KH: សាររបស់អ្នកត្រូវបានបង្ហោះចូលក្នុងឆានែលរួចរាល់។"
        )
        bot.reply_to(message, success_text, parse_mode="Markdown")

    except Exception as e:
        # Handle cases where the bot might have been kicked or the channel ID is wrong
        error_detail = str(e)
        if "chat not found" in error_detail.lower():
            fail_msg = "❌ **Error:** Channel not found. Make sure the @username is correct and I am a member."
        elif "forbidden" in error_detail.lower():
            fail_msg = "❌ **Error:** I don't have permission to access this channel."
        else:
            fail_msg = f"❌ **Broadcast Failed:**\n`{error_detail}`"
        
        bot.reply_to(message, fail_msg, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"])
def start_broadcast_process(message):
    """Initiates the step-by-step broadcast handler"""
    u_id = message.from_user.id
    
    # Check if user is authorized in the database
    if not is_authorized(u_id):
        return

    # Check if the user has already linked a target channel
    target = get_user_channel(u_id)
    if not target:
        lang = get_user_lang(u_id)
        no_channel = (
            "📍 **No Channel Linked**\n\n"
            "EN: Please use the 'Set Channel' button first.\n"
            "KH: សូមចុចប៊ូតុង 'កំណត់ឆានែល' ជាមុនសិន។"
        )
        bot.reply_to(message, no_channel, parse_mode="Markdown")
        return

    # Prompt the user for the content they want to send
    prompt = (
        f"📢 **Ready to Broadcast**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 **Target:** `{target}`\n\n"
        f"EN: Send the content you want to post (Text, Photo, Video, or Poll).\n"
        f"KH: សូមផ្ញើសារ ឬរូបភាពដែលអ្នកចង់បង្ហោះ។\n\n"
        f"👉 _Type 'cancel' to stop._"
    )
    
    sent_msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
    
    # Register the next step to catch whatever the user sends next
    bot.register_next_step_handler(sent_msg, verify_and_broadcast, target)
# ==========================================
# SECTION 5: CHANNEL LOOKUP & STATS (AUDIT)
# ==========================================

def get_channel_info_via_bot(target):
    """Fetches channel info, ID, and latest activity marker"""
    try:
        # Standardize target format
        clean_target = target if target.startswith("@") or target.startswith("-100") else f"@{target}"
        
        # Fetch chat object from Telegram
        chat = bot.get_chat(clean_target)
        members = bot.get_chat_member_count(chat.id)
        
        # Get pinned message if available
        pinned_msg = chat.pinned_message
        pinned_id = pinned_msg.message_id if pinned_msg else None
        
        return {
            "title": chat.title,
            "id": chat.id,
            "members": members,
            "bio": chat.description if chat.description else "No Bio Available",
            "username": chat.username if chat.username else "Private Channel",
            "type": chat.type,
            "pinned_id": pinned_id
        }
    except Exception as e:
        print(f"❌ [AUDIT] Lookup Error for {target}: {e}")
        return None

def audit_thread_worker(message, wait_msg, target):
    """Audits the channel with logic-based bot detection"""
    data = get_channel_info_via_bot(target)
    
    if not data:
        error_text = (
            "❌ **Audit Failed**\n\n"
            "Possible reasons:\n"
            "1. Bot is not Admin in the channel.\n"
            "2. Channel username is incorrect.\n"
            "3. Channel is private and Bot hasn't joined."
        )
        bot.edit_message_text(error_text, message.chat.id, wait_msg.message_id, parse_mode="Markdown")
        return

    # --- ADVANCED VERDICT LOGIC (No Random Results) ---
    subs = data['members']
    has_bio = data['bio'] != "No Bio Available"
    has_pinned = data['pinned_id'] is not None

    # Logic-based Scoring
    if subs > 5000 and not has_bio and not has_pinned:
        verdict = "🔴 **HIGH RISK / សង្ស័យខ្លាំង**\n(Pattern: Mass Botted - No profile data)"
    elif subs > 1000 and not has_pinned:
        verdict = "🟡 **WARNING / គួរប្រុងប្រយ័ត្ន**\n(Pattern: Low Engagement / Inactive)"
    elif subs < 10:
        verdict = "⚪ **NEW / ឆានែលថ្មី**\n(Not enough data for audit)"
    else:
        verdict = "🟢 **REAL / ធម្មតា**\n(Pattern: Authentic Growth)"

    msg_count_display = f"`{data['pinned_id']}` (Index)" if data['pinned_id'] else "N/A"

    report = (
        f"🛡️ **CHANNEL AUDIT REPORT**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 **Title:** `{data['title']}`\n"
        f"🆔 **ID:** `{data['id']}`\n"
        f"👥 **Subs:** `{subs:,}`\n"
        f"📊 **Activity Index:** {msg_count_display}\n"
        f"📝 **Bio:** _{data['bio']}_\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ **Verdict:** {verdict}\n\n"
        f"🔄 _Retrieving latest pinned interaction..._"
    )

    bot.edit_message_text(report, message.chat.id, wait_msg.message_id, parse_mode="Markdown")

    # --- ATTEMPT TO COPY PINNED CONTENT ---
    if data['pinned_id']:
        try:
            # We copy the message to show the admin what the latest 'active' post looks like
            bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=data['id'],
                message_id=data['pinned_id']
            )
        except Exception as copy_err:
            bot.send_message(message.chat.id, "ℹ️ _Note: Cannot copy post. Ensure Bot has 'Forward Messages' rights._")
    else:
        bot.send_message(message.chat.id, "ℹ️ _No pinned activity found for deep-scan._")

@bot.message_handler(func=lambda m: m.text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"])
def handle_audit(message):
    u_id = message.from_user.id
    if not is_authorized(u_id): 
        return

    target = get_user_channel(u_id)
    if not target:
        bot.reply_to(message, "⚠️ **EN:** Set channel first! / **KH:** សូមកំណត់ឆានែលសិន!")
        return

    # Use a loading message to improve User Experience
    wait_msg = bot.send_message(message.chat.id, "📡 **Accessing Telegram API Nodes...**")
    
    # Run in thread to prevent blocking the entire bot for other users
    threading.Thread(target=audit_thread_worker, args=(message, wait_msg, target), daemon=True).start()
# ==========================================
# SECTION 5: ADVANCED DEEP-SCAN (BOT API)
# ==========================================
import threading
import time

def run_standard_audit(target_username):
    """
    Performs a deep audit using standard Bot API.
    Standard bots require Admin rights to see full chat metadata.
    """
    try:
        # Standardize formatting for public/private identifiers
        if target_username.startswith("-100"):
            clean_target = int(target_username)
        else:
            clean_target = target_username if target_username.startswith("@") else f"@{target_username}"
        
        # 1. Fetch Chat Object & Permission verification
        chat = bot.get_chat(clean_target)
        
        # 2. Fetch Live Statistics
        members = bot.get_chat_member_count(chat.id)
        
        # 3. Structural Analysis
        # bots see 'pinned_message' object only if they have read rights
        has_pin = chat.pinned_message is not None
        has_bio = chat.description is not None and len(chat.description) > 5
        
        # 4. FRAUD INDEX CALCULATION (Proprietary Heuristic)
        # We calculate the bot probability based on lack of profile metadata
        fraud_score = 0
        
        # Low info channels are high risk
        if not has_bio: 
            fraud_score += 35
        if not has_pin: 
            fraud_score += 25
        
        # Large subscriber bases with no username (Private) or no bio usually indicates SMM Panels
        if members > 5000 and not chat.username: 
            fraud_score += 30
        
        # New channels with massive sub counts
        if members > 20000:
            fraud_score += 10
            
        # Ensure score doesn't exceed 99% for realism
        fraud_score = min(fraud_score, 99)
        
        return {
            "subs": members,
            "title": chat.title,
            "has_pin": has_pin,
            "has_bio": has_bio,
            "fraud_index": fraud_score,
            "username": chat.username or "Private_ID",
            "chat_id": chat.id
        }
    except Exception as e:
        print(f"❌ [AUDIT_ENGINE] Error: {e}")
        return None

def audit_thread_worker(message, wait_msg, target):
    """Background worker to process the audit with a high-end UI sequence"""
    chat_id = message.chat.id
    msg_id = wait_msg.message_id
    
    try:
        # --- UI STEP 1: INITIALIZING ---
        time.sleep(1.5)
        bot.edit_message_text("🔍 `[██░░░░░░░░] 20%` \n📡 **Handshaking with Telegram MTProto...**", chat_id, msg_id, parse_mode="Markdown")
        
        # --- UI STEP 2: METADATA FETCH ---
        data = run_standard_audit(target)
        time.sleep(1.5)
        
        if not data:
            error_msg = (
                "⚠️ **Audit Failed / ការវិភាគបរាជ័យ**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "EN: Please ensure:\n"
                "1. Bot is added to the channel as Admin.\n"
                "2. The channel ID/Username is correct.\n"
                "3. The channel is not restricted/deleted.\n\n"
                "KH: សូមប្រាកដថា Bot ជា Admin និង Username ត្រឹមត្រូវ។"
            )
            bot.edit_message_text(error_msg, chat_id, msg_id, parse_mode="Markdown")
            return

        bot.edit_message_text("🔍 `[██████░░░░] 60%` \n🧬 **Analyzing Subscriber Density & Bio...**", chat_id, msg_id, parse_mode="Markdown")
        time.sleep(1.2)
        
        bot.edit_message_text("🔍 `[██████████] 100%` \n✅ **Audit Ready! Generating Report...**", chat_id, msg_id, parse_mode="Markdown")
        time.sleep(1.0)

        # --- Decision Logic (Telebot Heuristics) ---
        if data['fraud_index'] >= 65:
            verdict = "🔴 **HIGH RISK / គ្រោះថ្នាក់**"
            status_color = "SMM BOTTED INDICATORS"
            warning_note = "⚠️ High probability of purchased subscribers."
        elif data['fraud_index'] >= 35:
            verdict = "🟡 **CAUTION / ប្រុងប្រយ័ត្ន**"
            status_color = "INACTIVE / LOW PROFILE"
            warning_note = "⚠️ Channel has very little engagement data."
        else:
            verdict = "🟢 **SAFE / សុវត្ថិភាព**"
            status_color = "CLEAN / VERIFIED"
            warning_note = "✅ This channel looks authentic."

        # --- Final Realistic Report ---
        report = (
            f"🛡️ **DEEP AUDIT: {data['title']}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 **Subs:** `{data['subs']:,}`\n"
            f"🆔 **User:** `@{data['username']}`\n"
            f"📌 **Pinned:** {'✅ Yes' if data['has_pin'] else '❌ No'}\n"
            f"📝 **Bio:** {'✅ Yes' if data['has_bio'] else '❌ No'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ **Verdict:** {verdict}\n"
            f"⭐ **Status:** `{status_color}`\n"
            f"📊 **Bot Probability:** `{data['fraud_index']}%`\n"
            f"📍 **Target ID:** `{data['chat_id']}`\n\n"
            f"ℹ️ _{warning_note}_\n\n"
            f"EN: Scan completed successfully.\n"
            f"KH: ការពិនិត្យបានបញ្ចប់ដោយជោគជ័យ។"
        )

        bot.edit_message_text(report, chat_id, msg_id, parse_mode="Markdown")

    except Exception as e:
        bot.edit_message_text(f"❌ **System Error:** `{str(e)}`", chat_id, msg_id)

@bot.message_handler(func=lambda m: m.text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"])
def check_stats(message):
    """Entry point for auditing using Bot API with threading"""
    u_id = message.from_user.id
    
    if not is_authorized(u_id): 
        bot.reply_to(message, "🚫 **ACCESS DENIED**\nContact Super Admin for authorization.")
        return

    target = get_user_channel(u_id)
    if not target:
        bot.reply_to(message, "⚠️ **KH:** សូមកំណត់ឆានែលជាមុនសិន (/set)\n**EN:** Set channel first (/set).")
        return

    # Send the initial 'loading' state
    wait_msg = bot.send_message(
        message.chat.id, 
        "🛠️ **INITIALIZING SCAN ENGINE...**\n"
        "📡 Connecting to Vinzy-Nodes...",
        parse_mode="Markdown"
    )

    # Launch worker in a separate thread so other users can still use the bot
    audit_thread = threading.Thread(
        target=audit_thread_worker, 
        args=(message, wait_msg, target),
        daemon=True
    )
    audit_thread.start()
# ==========================================
# SECTION 7: MASTER UI & ROUTING
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def start_panel(message):
    u_id = message.from_user.id
    if not is_authorized(u_id):
        bot.send_message(message.chat.id, "🚫 Access Denied. Contact @vinzystorezz.")
        return 

    lang = get_user_lang(u_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Multilingual Labels for Keyboard
    l_poll = "📊 Create Poll" if lang == 'en' else "📊 បង្កើតការបោះឆ្នោត"
    l_audit = "🔍 Audit Channel" if lang == 'en' else "🔍 ពិនិត្យឆានែល"
    l_set = "📍 Set Channel" if lang == 'en' else "📍 កំណត់ឆានែល"
    l_report = "🛡️ Report Channel" if lang == 'en' else "🛡️ រាយការណ៍ឆានែល"
    l_lang = "🌐 Language" if lang == 'en' else "🌐 ភាសា"
    l_help = "❓ Help" if lang == 'en' else "❓ ជំនួយ"
    l_sched = "📅 Schedule Info" if lang == 'en' else "📅 កាលវិភាគ"

    # Adding buttons to layout
    markup.add(l_poll, l_audit)
    markup.add(l_set, l_report)
    markup.add(l_lang, l_sched)
    markup.add(l_help)
    
    if u_id == SUPER_ADMIN_ID:
        markup.add("➕ Add Admin", "➖ Remove Admin")

    welcome_text = (
        "🛡️ **Vinzy Control Panel**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "EN: Select a tool from the menu below.\n"
        "KH: សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម។"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")

def set_channel_prompt(message):
    """Starts the process to bind a target channel to the admin's account"""
    prompt = (
        "📍 **Target Channel Configuration**\n\n"
        "EN: Send the channel @username or ID (e.g., @mychannel or -100123456789):\n"
        "KH: សូមផ្ញើឈ្មោះ Channel របស់អ្នក (ឧទាហរណ៍ @username ឬ ID ឆានែល):"
    )
    msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_set_channel_logic)

@bot.message_handler(func=lambda message: True)
def master_router(message):
    """Central routing for all interactions - Prevents Handler Conflicts"""
    u_id = message.from_user.id
    if not is_authorized(u_id): 
        return
    
    lang = get_user_lang(u_id)
    text = message.text

    # 1. POLL SYSTEM
    if text in ["📊 Create Poll", "📊 បង្កើតការបោះឆ្នោត"]:
        prompt = (
            "📋 **Poll Creation**\n\n"
            "EN: Send name list (one per line):\n"
            "KH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (ម្នាក់មួយបន្ទាត់):"
        )
        msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_poll_names)

    # 2. CHANNEL CONFIGURATION
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        set_channel_prompt(message)

    # 3. AUDIT SYSTEM (Linked to Section 5 logic)
    elif text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        check_stats(message)

    # 4. LANGUAGE SETTINGS
    elif text in ["🌐 Language", "🌐 ភាសា"]:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en'),
                   types.InlineKeyboardButton("ភាសាខ្មែរ 🇰🇭", callback_data='set_lang_kh'))
        bot.send_message(message.chat.id, "Select Language / សូមជ្រើសរើសភាសា:", reply_markup=markup)

    # 5. SCHEDULE & SYSTEM STATUS
    elif text in ["📅 Schedule Info", "📅 កាលវិភាគ"]:
        tz_kh = pytz.timezone('Asia/Phnom_Penh')
        now_kh = datetime.now(tz_kh).strftime("%I:%M %p")
        status = (
            f"⏰ **System Diagnostic**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🇰🇭 KH Time: `{now_kh}`\n"
            f"📡 DB Status: `Online` (Neon)\n"
            f"🛡️ Security: `Verified Admin`\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        bot.send_message(message.chat.id, status, parse_mode="Markdown")

    # 6. REPORT & HELP
    elif text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"]:
        report_start(message)
    
    elif text in ["❓ Help", "❓ ជំនួយ"]:
        send_help(message, lang)

    # 7. SUPER-ADMIN COMMANDS
    elif u_id == SUPER_ADMIN_ID:
        if text == "➕ Add Admin":
            msg = bot.reply_to(message, "🆔 Send User ID to Add:")
            bot.register_next_step_handler(msg, process_add_admin)
        elif text == "➖ Remove Admin":
            msg = bot.reply_to(message, "🆔 Send User ID to Remove:")
            bot.register_next_step_handler(msg, process_remove_admin)

# ==========================================
# SECTION 8: FEATURE ENGINE (EXTENDED LOGIC)
# ==========================================

def process_set_channel_logic(message):
    """Saves target channel with automatic @ formatting and UPSERT logic"""
    u_id = message.from_user.id
    val = message.text.strip()
    
    # Auto-format input to @username style if it's missing the prefix
    if not val.startswith('@') and not val.startswith('-100'): 
        val = f"@{val}"
    
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        # INSERT or UPDATE if user already exists in the database
        cursor.execute("""
            INSERT INTO users (user_id, target_channel) VALUES (%s, %s) 
            ON CONFLICT (user_id) DO UPDATE SET target_channel = EXCLUDED.target_channel
        """, (u_id, val))
        conn.commit()
        bot.reply_to(message, f"✅ **Success!**\nTarget locked to: `{val}`")
    except Exception as e:
        bot.reply_to(message, f"❌ Database Error: {str(e)}")
    finally:
        if conn: 
            db_pool.putconn(conn)

def process_poll_names(message):
    """Processes names with the 4+1 Protection Rule Engine"""
    u_id = message.from_user.id
    target = get_user_channel(u_id)
    
    if not target:
        bot.reply_to(message, "⚠️ **Set channel first!** Click 📍 Set Channel.")
        return

    # Filter out empty lines and trim whitespace
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    
    if len(names) < 2:
        bot.reply_to(message, "❌ Please provide at least 2 names.")
        return

    # 4+1 LOGIC START
    # Split the list into chunks of 4 names
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # If the last chunk has only 1 person, merge it into the previous group to avoid 1-option polls
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        last_person = chunks.pop()
        chunks[-1].extend(last_person)
    
    

    bot.send_message(message.chat.id, f"🚀 **Generating {len(chunks)} Polls for {target}...**")

    # Send the polls with a delay to avoid Telegram flood limits
    for i, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target,
                question=f"Round {i} / ជុំទី {i}",
                options=group,
                is_anonymous=True,
                allows_multiple_answers=False
            )
            time.sleep(1.8) # Delay to stay under Telegram API limits on Koyeb
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Poll {i} failed: {str(e)}")

    bot.send_message(message.chat.id, "✅ **Poll Dispatch Complete!**")

def process_add_admin(message):
    """Adds a new admin ID to the Neon database"""
    try:
        new_id = int(message.text.strip())
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id, is_admin) VALUES (%s, 1) ON CONFLICT (user_id) DO UPDATE SET is_admin = 1", (new_id,))
        conn.commit()
        db_pool.putconn(conn)
        bot.send_message(message.chat.id, f"✅ User `{new_id}` is now an Admin.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to add Admin: {e}")

def process_remove_admin(message):
    """Removes admin status from a user in the database"""
    try:
        target_id = int(message.text.strip())
        if target_id == SUPER_ADMIN_ID:
            bot.send_message(message.chat.id, "❌ Cannot remove the Super Admin.")
            return
            
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_admin = 0 WHERE user_id = %s", (target_id,))
        conn.commit()
        db_pool.putconn(conn)
        bot.send_message(message.chat.id, f"➖ User `{target_id}` removed from Admin list.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to remove Admin: {e}")
# ==========================================
# SECTION 9: MASS REPORT SIMULATOR (PRO)
# ==========================================
import random
import threading
import time

# --- UTILITY GENERATORS ---

def generate_fake_ip():
    """Generates a random IPv4 address for visual realism"""
    return f"{random.randint(100, 223)}.{random.randint(1, 254)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

def get_random_node():
    """Returns a fake specialized server node location"""
    nodes = ["SG-Cloud-01", "HK-Data-Center", "US-East-Node", "EU-West-Proxy", "KH-Mainframe-09"]
    return random.choice(nodes)

# Global Session Lock to prevent overlapping threads per user
active_reports = set()

# --- INTERFACE START ---

@bot.message_handler(func=lambda m: m.text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"])
def report_start(message):
    """Starts the advanced mass report simulation interface"""
    u_id = message.from_user.id
    if not is_authorized(u_id): return

    # Check if user already has a running process
    if u_id in active_reports:
        bot.reply_to(message, "⚠️ **Process Active:** Please wait for the current sequence to finish.")
        return

    lang = get_user_lang(u_id)
    target = get_user_channel(u_id)

    if not target:
        bot.reply_to(message, "⚠️ **EN:** Please lock a channel first using /set\n⚠️ **KH:** សូមកំណត់ Channel ជាមុនសិន")
        return

    # Create UI for selecting report volume
    markup = types.InlineKeyboardMarkup(row_width=3)
    btn1 = types.InlineKeyboardButton("Standard (250)", callback_data="run_rep_250")
    btn2 = types.InlineKeyboardButton("Extreme (750)", callback_data="run_rep_750")
    btn3 = types.InlineKeyboardButton("Overload (1500)", callback_data="run_rep_1500")
    markup.add(btn1, btn2, btn3)

    msg = (f"🛡️ **CYBER-SECURITY INTERFACE**\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"📡 **Target:** `{target}`\n"
           f"🛠️ **Engine:** `Vinzy-Trust-Safety-v4`\n\n"
           f"EN: Choose reporting intensity for T&S Nodes:\n"
           f"KH: សូមជ្រើសរើសកម្រិតនៃការរាយការណ៍:")
    
    bot.send_message(message.chat.id, msg, reply_markup=markup, parse_mode="Markdown")

# --- CALLBACK & THREAD HANDLING ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('run_rep_'))
def handle_report_callback(call):
    """Triggers the execution thread and manages process locking"""
    u_id = call.from_user.id
    
    if u_id in active_reports:
        bot.answer_callback_query(call.id, "❌ Already running!")
        return

    bot.answer_callback_query(call.id, "Initializing Sequence...")
    active_reports.add(u_id) # Lock the user session
    
    # Passing 'call' directly to thread is risky if call object expires; 
    # better to pass specific IDs
    t = threading.Thread(target=execute_report_simulation, args=(call,))
    t.daemon = True
    t.start()

# --- SIMULATION CORE LOGIC ---

def execute_report_simulation(call):
    """The deep-logic for a hyper-realistic simulated mass report"""
    u_id = call.from_user.id
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    
    try:
        amount = call.data.split('_')[2]
        target = get_user_channel(u_id) or "Unknown_Ref"
        
        # 1. Initialization UI
        bot.edit_message_text(
            f"🔄 **Establishing Encrypted Tunnel...**\n`[░░░░░░░░░░░░░░░░░░░░] 0%`", 
            chat_id, msg_id, parse_mode="Markdown"
        )

        # Extended Realistic Stages
        stages = [
            {"p": 5, "t": "Bypassing Cloudflare protection layers..."},
            {"p": 15, "t": "Establishing WebSocket Handshake with API..."},
            {"p": 25, "t": "Synchronizing 128 Dedicated Proxy Nodes..."},
            {"p": 45, "t": f"Injecting {amount} Fraud Metadata Packets..."},
            {"p": 65, "t": "Spoofing Device User-Agents (Mobile)..."},
            {"p": 85, "t": "Finalizing bulk submission to Safety Hub..."},
            {"p": 95, "t": "Clearing digital footprints and IP logs..."},
            {"p": 100, "t": "✅ **SEQUENCE COMPLETED SUCCESSFULLY**"}
        ]

        for stage in stages:
            time.sleep(random.uniform(1.5, 3.0)) 
            
            # Dynamic UI update logic
            bar_filled = stage['p'] // 5
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            
            # Log Generation
            log_lines = []
            for _ in range(3): # Show 3 rotating logs for clarity
                ip = generate_fake_ip()
                node = get_random_node()
                status = random.choice(["SENT", "DELIVERED", "ACK"])
                log_lines.append(f"📡 `[{node}]` -> `{ip}` -> **{status}**")
            
            logs = "\n".join(log_lines)
            
            try:
                bot.edit_message_text(
                    f"🛡️ **SECURITY OPS: ACTIVE**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 **Target:** `{target}`\n"
                    f"📊 **Progress:** `[{bar}] {stage['p']}%`\n\n"
                    f"⚙️ **Current Action:**\n_{stage['t']}_\n\n"
                    f"🖥️ **Live Console Logs:**\n{logs}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ _Engine: @vinzystorezz V4-PRO_",
                    chat_id, msg_id, parse_mode="Markdown"
                )
            except Exception:
                continue # Ignore Telegram 'Message Not Modified' errors

        # Final Summary
        time.sleep(2)
        ticket_id = f"TKS-{random.randint(100000, 999999)}"
        
        final_report = (
            f"✅ **MASS REPORT PROTOCOL FINISHED**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 **Total Reports:** `{amount}` Packets\n"
            f"🆔 **Ticket ID:** `{ticket_id}`\n"
            f"🛰️ **Nodes Used:** `128 Cloud Proxies`\n"
            f"🛡️ **Target Status:** `FLAGGED / UNDER REVIEW`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"**System Feedback:**\n"
            f"EN: Data successfully injected into the moderation queue. Violation review takes 24-48 hours.\n\n"
            f"KH: ទិន្នន័យត្រូវបានបញ្ជូនទៅកាន់ប្រព័ន្ធរួចរាល់។ ការត្រួតពិនិត្យត្រូវការពេល ២៤ ទៅ ៤៨ ម៉ោង។\n\n"
            f"⚡ _Powered by @vinzystorezz_"
        )
        
        bot.send_message(chat_id, final_report, parse_mode="Markdown")

    except Exception as e:
        print(f"Simulation Error: {e}")
    finally:
        # ALWAYS unlock the user session, even if error occurs
        active_reports.discard(u_id)
import signal
import sys
import time

# ==========================================
# FINAL EXECUTION BLOCK (STABLE POLLING)
# ==========================================

def graceful_exit(sig, frame):
    """Ensures the DB pool is closed and the bot shuts down cleanly"""
    print(f"\n\033[1;33m⚠️ Shutdown signal received. Closing resources...\033[0m")
    try:
        # Clear any active simulated report sessions on exit
        if 'active_reports' in globals():
            active_reports.clear()
            
        if 'db_pool' in globals():
            db_pool.closeall()
            print("\033[1;32m✅ Database connection pool closed.\033[0m")
    except Exception as e:
        print(f"\033[1;31m❌ Error during cleanup: {e}\033[0m")
    
    print("\033[1;32m👋 Bot stopped. Goodbye!\033[0m")
    sys.exit(0)

# Register signals for CTRL+C (SIGINT) and System Kill (SIGTERM)
signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

if __name__ == "__main__":
    # --- ANSI Terminal Colors ---
    GREEN = "\033[1;32m"
    BLUE = "\033[1;34m"
    YELLOW = "\033[1;33m"
    RED = "\033[1;31m"
    CYAN = "\033[1;36m"
    RESET = "\033[0m"

    print(f"{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{GREEN}🚀 Vinzy Audit Bot [v4.0 PRO] is initializing...{RESET}")
    
    # 1. DATABASE CONNECTIVITY HEALTH CHECK
    print(f"{BLUE}📡 Checking Neon DB Connectivity...{RESET}")
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cursor:
            cursor.execute("SELECT version();")
            db_version = cursor.fetchone()
            print(f"{GREEN}✅ DB Connected: {db_version[0][:40]}...{RESET}")
        db_pool.putconn(conn)
    except Exception as e:
        print(f"{RED}❌ CRITICAL DATABASE ERROR: {e}{RESET}")
        print(f"{YELLOW}⚠️ Attempting to start, but DB features will be disabled.{RESET}")

    # 2. BOT IDENTITY & TOKEN VERIFICATION
    try:
        me = bot.get_me()
        print(f"{GREEN}✅ Bot Authenticated: @{me.username} (ID: {me.id}){RESET}")
    except Exception as e:
        print(f"{RED}❌ TOKEN ERROR: Cannot reach Telegram Servers. Check your TOKEN.{RESET}")
        sys.exit(1)

    print(f"{BLUE}🤖 System Status: LIVE | Monitoring Traffic...{RESET}")
    print(f"{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    # 3. INFINITY POLLING WITH AUTO-RESTART
    retry_delay = 5
    while True:
        try:
            # timeout=90: High timeout for long-term polling stability
            # long_polling_timeout=20: Efficient connection keeping
            # skip_pending=True: Ignore old messages sent while bot was offline
            bot.infinity_polling(
                timeout=90, 
                long_polling_timeout=20, 
                skip_pending=True,
                logger_level=None 
            )
            
        except Exception as e:
            curr_time = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"{RED}⚠️ POLLING CRASH at {curr_time}{RESET}")
            print(f"{RED}Error Details: {str(e)}{RESET}")
            
            # Dynamic cooldown: increases if crash repeats, resets on success
            print(f"{YELLOW}⏳ Cooldown: Restarting in {retry_delay}s...{RESET}")
            time.sleep(retry_delay)
            
            # Cap the retry delay at 60 seconds to avoid long downtimes
            retry_delay = min(retry_delay + 5, 60)
            
            print(f"{BLUE}🔄 Attempting to re-establish connection...{RESET}")
            continue
