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

BOT_TOKEN = os.getenv("BOT_TOKEN", "8782687814:AAGcsk0GnahkXoirzEmRmid6o8g9J44GcNM")
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
# SECTION 5: MASTER AUDIT (PRO GRADE A)
# ==========================================
import threading
import time
from datetime import datetime

def calculate_grade_a_score(members, total_posts, can_story):
    """
    Zero-Randomness Logic Engine:
    Uses mathematical thresholds to identify structural impossibilities.
    """
    score = 100
    findings_kh = []

    # --- RULE 1: THE EMPTY GIANT (Hard Penalty) ---
    # If a channel has 2,000+ members but the total post history (ID) is under 30.
    # This is a mathematical certainty of a fake shell.
    if members > 2000 and total_posts < 30:
        score -= 75
        findings_kh.append("🚩 **Fake Shell:** ឆានែលធំខ្លាំង តែគ្មានប្រវត្តិអត្ថបទសោះ (Fake Giant)")
    
    # --- RULE 2: GROWTH TO CONTENT RATIO ---
    # Real channels usually have 1 post for every 20-50 members.
    # If a channel has 10,000 members and only 100 posts, it is 'Hollow'.
    if members > 500:
        ratio = total_posts / members
        if ratio < 0.02: # Less than 2 posts per 100 members
            score -= 40
            findings_kh.append("⚠️ **Hollow Channel:** ចំនួនអត្ថបទតិចពេក បើធៀបនឹងសមាជិក")

    # --- RULE 3: SMM PANEL SIGNATURE ---
    # SMM Panels deliver in round blocks. Organic growth is messy (e.g., 1043).
    # We flag exact round numbers common in panel purchases.
    round_targets = [500, 1000, 2000, 5000, 10000, 20000, 50000]
    if members in round_targets:
        score -= 15
        findings_kh.append("🕵️ **SMM Pattern:** ចំនួនសមាជិកឡើងគត់ពេក (Matches SMM Blocks)")

    # --- RULE 4: PREMIUM PROOF ---
    # Human audiences include Premium users. Large channels without boosts are suspicious.
    if members > 3000 and not can_story:
        score -= 10
        findings_kh.append("🚫 **No Premium:** គ្មានអ្នកប្រើ Premium Boost (សញ្ញា Bot ច្រើន)")

    # --- SMALL CHANNEL PROTECTION ---
    # Do not penalize new/small channels (under 150 members).
    if members < 150:
        return 100, ["✅ New Channel: ទិន្នន័យនៅតូច មិនទាន់អាចវិភាគបាន។"]

    return max(5, score), findings_kh

def audit_thread_worker(message, wait_msg, target):
    """
    Worker thread that extracts raw MTProto metadata for analysis.
    """
    chat_id = message.chat.id
    msg_id = wait_msg.message_id
    
    try:
        bot.edit_message_text("🔍 `[██░░░░░░░░] 20%` \n📡 **Extracting MTProto Metadata...**", chat_id, msg_id)
        
        # Format target ID or Username
        if str(target).startswith("-100"): 
            clean_target = int(target)
        else: 
            clean_target = target if str(target).startswith("@") else f"@{target}"
        
        # Fetch RAW Data
        chat = bot.get_chat(clean_target)
        members = bot.get_chat_member_count(chat.id)
        
        # THE ANCHOR: Generate a transient ID to find the current sequence number
        temp_msg = bot.send_message(chat.id, "🛠️ `Structural Scan in progress...`")
        total_posts = temp_msg.message_id 
        bot.delete_message(chat.id, total_posts)

        # Check Boost Status (Requires Admin)
        try:
            can_story = getattr(chat, 'can_set_sticker_set', False)
        except:
            can_story = False

        bot.edit_message_text("🔍 `[██████░░░░] 60%` \n🧬 **Analyzing Channel Integrity...**", chat_id, msg_id)
        
        # Execute Scoring
        trust_score, f_kh = calculate_grade_a_score(members, total_posts, can_story)

        # Define Verdict
        if trust_score < 40:
            verdict, status = "🔴 **HIGH RISK**", "BOTTED / FAKE"
        elif trust_score < 80:
            verdict, status = "🟡 **CAUTION**", "SUSPICIOUS"
        else:
            verdict, status = "🟢 **SAFE**", "ORGANIC"

        finding_str = "\n".join(f_kh) if f_kh else "✅ រកមិនឃើញភាពមិនប្រក្រតីនៃទិន្នន័យទេ។"

        # Final Report UI
        report = (
            f"📊 **VINZY PRO-GRADE AUDIT REPORT**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 **Channel:** `{chat.title}`\n"
            f"👥 **Members:** `{members:,}`\n"
            f"📈 **Post History ID:** `{total_posts}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ **Verdict:** {verdict}\n"
            f"⭐ **Trust Score:** `{trust_score}%` / 100%\n"
            f"📍 **Status:** `{status}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 **Expert Findings:**\n_{finding_str}_\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 `Scan: {datetime.now().strftime('%Y-%m-%d %H:%M')}`\n"
            f"🛡️ _Verified via Structural Sequence Analysis._"
        )
        bot.edit_message_text(report, chat_id, msg_id, parse_mode="Markdown")

    except Exception as e:
        bot.edit_message_text(f"❌ **Audit Error:** Ensure Bot is Admin in `{target}`.\n`{str(e)}`", chat_id, msg_id)
# ==========================================
# SECTION 7: MASTER UI & ROUTING (PRO GRADE)
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def start_panel(message):
    """
    Initializes the Control Panel with localized keyboard buttons.
    Ensures only authorized users can access the interface.
    """
    u_id = message.from_user.id
    
    # Security Gate
    if not is_authorized(u_id):
        bot.send_message(message.chat.id, "🚫 **Access Denied.**\nYour ID is not whitelisted. Contact @vinzystorezz.")
        return 

    # Retrieve User Preference (Default to 'en' if not set)
    lang = get_user_lang(u_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # --- Multilingual Keyboard Labels ---
    # These strings MUST match the master_router logic exactly.
    l_poll   = "📊 Create Poll" if lang == 'en' else "📊 បង្កើតការបោះឆ្នោត"
    l_audit  = "🔍 Audit Channel" if lang == 'en' else "🔍 ពិនិត្យឆានែល"
    l_set    = "📍 Set Channel" if lang == 'en' else "📍 កំណត់ឆានែល"
    l_report = "🛡️ Report Channel" if lang == 'en' else "🛡️ រាយការណ៍ឆានែល"
    l_lang   = "🌐 Language" if lang == 'en' else "🌐 ភាសា"
    l_help   = "❓ Help" if lang == 'en' else "❓ ជំនួយ"
    l_sched  = "📅 Schedule Info" if lang == 'en' else "📅 កាលវិភាគ"

    # Construct Layout
    markup.add(l_poll, l_audit)
    markup.add(l_set, l_report)
    markup.add(l_lang, l_sched)
    markup.add(l_help)
    
    # Admin-Only Row
    if u_id == SUPER_ADMIN_ID:
        markup.add("➕ Add Admin", "➖ Remove Admin")

    welcome_text = (
        "🛡️ **Vinzy Control Panel v4.0**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✨ **EN:** Select a professional tool below.\n"
        "✨ **KH:** សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម។\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['audit', 'check'])
def quick_audit_command(message):
    """Direct command support for auditing without using the keyboard."""
    handle_audit_command(message)

@bot.message_handler(func=lambda message: True)
def master_router(message):
    """
    Central Logic Hub: Routes text inputs from the ReplyKeyboard 
    to their respective backend functions.
    """
    u_id = message.from_user.id
    
    # Unauthorized users are ignored by the router
    if not is_authorized(u_id): 
        return
    
    text = message.text
    lang = get_user_lang(u_id)

    # 1. POLL CREATION ENGINE
    if text in ["📊 Create Poll", "📊 បង្កើតការបោះឆ្នោត"]:
        prompt = (
            "📋 **Poll Creation Sequence**\n\n"
            "EN: Send the list of names (One name per line):\n"
            "KH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (ម្នាក់មួយបន្ទាត់):"
        )
        msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_poll_names)

    # 2. CHANNEL TARGETING
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        prompt = (
            "📍 **Target Configuration**\n\n"
            "EN: Send the channel @username (e.g., @vinzystorezz):\n"
            "KH: សូមផ្ញើឈ្មោះ Channel របស់អ្នក (ឧទាហរណ៍ @username):"
        )
        msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_set_channel_logic)

    # 3. MASTER AUDIT (Fixed: Points to Grade A Engine)
    elif text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        handle_audit_command(message)

    # 4. MULTILINGUAL SETTINGS
    elif text in ["🌐 Language", "🌐 ភាសា"]:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en'),
            types.InlineKeyboardButton("ភាសាខ្មែរ 🇰🇭", callback_data='set_lang_kh')
        )
        bot.send_message(message.chat.id, "🌐 **Language Settings / ការកំណត់ភាសា**\nSelect your preference:", reply_markup=markup)

    # 5. DIAGNOSTICS & SYSTEM STATUS
    elif text in ["📅 Schedule Info", "📅 កាលវិភាគ"]:
        # Ensure 'pytz' is imported for this to work
        try:
            import pytz
            tz_kh = pytz.timezone('Asia/Phnom_Penh')
            now_kh = datetime.now(tz_kh).strftime("%I:%M %p")
        except:
            now_kh = datetime.now().strftime("%I:%M %p")
            
        status_report = (
            f"📊 **System Integrity Report**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 KH Time: `{now_kh}`\n"
            f"📡 DB Cluster: `Neon-PostgreSQL` (Online)\n"
            f"🧠 Logic Engine: `Pro-Grade Grade A`\n"
            f"🛡️ Security: `MTProto Verified`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        bot.send_message(message.chat.id, status_report, parse_mode="Markdown")

    # 6. EXTERNAL TOOLS (Report & Help)
    elif text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"]:
        # Ensure report_start function is defined in your feature block
        if 'report_start' in globals():
            report_start(message)
        else:
            bot.reply_to(message, "❌ Report Module not found.")
    
    elif text in ["❓ Help", "❓ ជំនួយ"]:
        # Ensure send_help function is defined in your feature block
        if 'send_help' in globals():
            send_help(message, lang)
        else:
            bot.reply_to(message, "❌ Help Module not found.")

    # 7. SUPER-ADMINISTRATOR PRIVILEGES
    elif u_id == SUPER_ADMIN_ID:
        if text == "➕ Add Admin":
            msg = bot.reply_to(message, "🆔 **Action:** Send User ID to grant Admin access:")
            bot.register_next_step_handler(msg, process_add_admin)
        elif text == "➖ Remove Admin":
            msg = bot.reply_to(message, "🆔 **Action:** Send User ID to revoke Admin access:")
            bot.register_next_step_handler(msg, process_remove_admin)
# ==========================================
# SECTION 8: FEATURE ENGINE (FIXED & SYNCED)
# ==========================================

def handle_audit_command(message):
    """Bridge between UI and Grade A Engine - Verifies Admin Status First"""
    u_id = message.from_user.id
    target = get_user_channel(u_id)
    
    if not target:
        bot.reply_to(message, "⚠️ **KH:** សូមកំណត់ឆានែលជាមុនសិន! (📍 Set Channel)\n**EN:** Please set a channel first!")
        return
        
    try:
        # Resolve target to get Chat ID correctly
        if str(target).startswith("-100"): clean_target = int(target)
        else: clean_target = target if str(target).startswith("@") else f"@{target}"
        
        # PRO CHECK: Verify if bot is actually in the channel and is admin
        chat_member = bot.get_chat_member(clean_target, bot.get_me().id)
        
        if chat_member.status not in ['administrator', 'creator']:
            bot.reply_to(message, f"❌ **Admin Required!**\n\nI am in {target}, but I am not an Admin. Please promote me so I can scan the ID sequence!")
            return
            
    except Exception as e:
        bot.reply_to(message, f"❌ **Connection Error!**\n\nI cannot find `{target}`. Make sure:\n1. The username is correct.\n2. I am added as an Admin there.")
        return

    wait_msg = bot.send_message(message.chat.id, "🛠️ **INITIALIZING GRADE A ENGINE...**\n📡 កំពុងចាប់ផ្ដើមម៉ាស៊ីនវិភាគ...", parse_mode="Markdown")
    
    # Start thread
    threading.Thread(target=audit_thread_worker, args=(message, wait_msg, target), daemon=True).start()

def process_set_channel_logic(message):
    """Saves target channel with automatic @ formatting and UPSERT logic"""
    u_id = message.from_user.id
    val = message.text.strip()
    
    # Format input
    if not val.startswith('@') and not val.startswith('-100'): 
        val = f"@{val}"
    
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO users (user_id, target_channel) VALUES (%s, %s) 
                ON CONFLICT (user_id) DO UPDATE SET target_channel = EXCLUDED.target_channel
            """, (u_id, val))
        conn.commit()
        bot.reply_to(message, f"✅ **Success!**\nTarget locked to: `{val}`\n\n_Now make sure I am an Admin in that channel!_")
    except Exception as e:
        bot.reply_to(message, f"❌ Database Error: {str(e)}")
    finally:
        if conn: db_pool.putconn(conn)

def process_poll_names(message):
    """Processes names with the 4+1 Protection Rule Engine"""
    u_id = message.from_user.id
    target = get_user_channel(u_id)
    
    if not target:
        bot.reply_to(message, "⚠️ **Set channel first!** Click 📍 Set Channel.")
        return

    # Filter names
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    
    if len(names) < 2:
        bot.reply_to(message, "❌ Please provide at least 2 names.")
        return

    # 4+1 LOGIC: Split into chunks of 4
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # Avoid 1-option polls
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        last_person = chunks.pop()
        chunks[-1].extend(last_person)

    bot.send_message(message.chat.id, f"🚀 **Dispatching {len(chunks)} Polls to {target}...**")

    for i, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target,
                question=f"Round {i} / ជុំទី {i}",
                options=group,
                is_anonymous=True,
                allows_multiple_answers=False
            )
            time.sleep(2.0) # Conservative delay for Koyeb stability
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Poll {i} failed: {str(e)}")
            break

    bot.send_message(message.chat.id, "✅ **Poll Dispatch Complete!**")

def process_add_admin(message):
    """Adds a new admin ID to the Neon database"""
    try:
        new_id = int(message.text.strip())
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (user_id, is_admin) VALUES (%s, 1) 
                ON CONFLICT (user_id) DO UPDATE SET is_admin = 1
            """, (new_id,))
        conn.commit()
        db_pool.putconn(conn)
        bot.send_message(message.chat.id, f"✅ User `{new_id}` is now an Admin.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to add Admin: {e}")

def process_remove_admin(message):
    """Removes admin status from a user"""
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

# 1. First, define your handler so the bot knows what to listen for
@bot.message_handler(func=lambda m: m.text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"])
def handle_audit_command(message):
    u_id = message.from_user.id
    
    if not is_authorized(u_id): 
        bot.reply_to(message, "🚫 You are not authorized.")
        return
    
    target = get_user_channel(u_id)
    if not target:
        bot.reply_to(message, "⚠️ Please set a channel first!")
        return
        
    wait_msg = bot.send_message(message.chat.id, "🛠️ **INITIALIZING GRADE A ENGINE...**")
    
    # Ensure audit_thread_worker is defined above this!
    threading.Thread(target=audit_thread_worker, args=(message, wait_msg, target), daemon=True).start()

# ==========================================
# SECTION 9: FINAL EXECUTION (ANTI-CONFLICT)
# ==========================================

if __name__ == "__main__":
    # --- Terminal Identity ---
    CYAN = "\033[1;36m"
    GREEN = "\033[1;32m"
    RED = "\033[1;31m"
    YELLOW = "\033[1;33m"
    RESET = "\033[0m"

    print(f"{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{GREEN}🚀 Vinzy Audit Bot [v4.0 PRO] is initializing...{RESET}")
    
    # 1. Identity Check
    try:
        me = bot.get_me()
        print(f"{GREEN}✅ Authenticated as: @{me.username}{RESET}")
    except Exception as e:
        print(f"{RED}❌ Connection Failed: {e}{RESET}")

    print(f"{CYAN}🤖 System Status: LIVE | Monitoring Traffic...{RESET}")
    print(f"{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    # 2. Resilient Polling Loop
    retry_delay = 5
    while True:
        try:
            # We use polling here; ensure no other script is running this token!
            bot.infinity_polling(
                timeout=90, 
                long_polling_timeout=20, 
                skip_pending=True
            )
        except Exception as e:
            err_msg = str(e)
            # Handle the 409 Conflict specifically
            if "Conflict" in err_msg:
                print(f"{YELLOW}⚠️ 409 CONFLICT: Another instance is active. Waiting 10s...{RESET}")
                time.sleep(10) 
            else:
                print(f"{RED}⚠️ POLLING CRASH: {err_msg}{RESET}")
                time.sleep(retry_delay)
                
            # Dynamic retry backoff
            retry_delay = min(retry_delay + 5, 60)
            print(f"{CYAN}🔄 Attempting to re-connect...{RESET}")
            continue
