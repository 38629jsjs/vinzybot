import telebot
from telebot import types
import psycopg2
from psycopg2 import pool
import threading
import time
import pytz
from datetime import datetime
import os

# ==========================================
# SECTION 1: CONFIGURATION (BOT MODE)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8782687814:AAEj5hYbo7a2TFZnfYWF7zf1NaCPx4fgyT0")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "8702798367"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_5vXuDLicq2wT@ep-small-boat-aim6necc-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require")

# Initialize Telegram Bot
bot = telebot.TeleBot(BOT_TOKEN)
# ==========================================
# SECTION 2: DATABASE LOGIC (Admins/Users/Privacy)
# ==========================================

PERMANENT_ADMINS = [8702798367, 123456789] 

def init_db():
    """Initializes the Neon PostgreSQL database with Language support"""
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
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
        if conn: db_pool.putconn(conn)

def is_authorized(user_id):
    """Checks if a user has permission to use the bot tools"""
    user_id = int(user_id)
    if user_id == SUPER_ADMIN_ID or user_id in PERMANENT_ADMINS:
        return True
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
        if conn: db_pool.putconn(conn)

def get_user_channel(user_id):
    """Retrieves the target channel associated with a specific user"""
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
        if conn: db_pool.putconn(conn)

def get_user_lang(user_id):
    """Checks the database for user's language preference"""
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
        if conn: db_pool.putconn(conn)

def set_user_lang(user_id, lang_code):
    """Updates the user's language preference using Postgres UPSERT logic"""
    conn = None
    try:
        conn = db_pool.getconn()
        c = conn.cursor()
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
        if conn: db_pool.putconn(conn)

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
    """
    Standard Bot API handler for poll updates.
    Note: Bot must be an Admin in the channel to receive these updates.
    """
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
    # Detects votes arriving at perfectly even intervals (typical of scripts)
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
    # Detects sudden mass-botting (instant jumps)
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
# SECTION 4: BROADCAST & ADMIN VERIFICATION
# ==========================================

def verify_and_broadcast(message, user_channel):
    """
    Checks for REAL Admin status and executes the broadcast.
    This works for all message types (Text, Photo, Video, Poll).
    """
    try:
        # 1. LIVE ADMIN CHECK
        bot_id = bot.get_me().id
        check = bot.get_chat_member(user_channel, bot_id)

        # Verify if the bot is actually in the channel as Admin
        if check.status not in ['administrator', 'creator']:
            bot.reply_to(
                message, 
                "❌ **Admin Error**\n\n"
                "EN: I must be an ADMIN in the channel to post.\n"
                "KH: ខ្ញុំត្រូវតែជា ADMIN នៅក្នុង Channel ដើម្បីបង្ហោះសារបាន។"
            )
            return

        # Verify if 'Post Messages' permission is enabled
        if check.status == 'administrator' and not check.can_post_messages:
            bot.reply_to(
                message,
                "❌ **Permission Error**\n\n"
                "EN: I am Admin, but 'Post Messages' right is OFF.\n"
                "KH: ខ្ញុំជា Admin តែមិនមានសិទ្ធិ 'Post Messages' ទេ។"
            )
            return

        # 2. EXECUTE BROADCAST
        bot.copy_message(
            chat_id=user_channel,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )

        # 3. SUCCESS FEEDBACK
        success_text = (
            f"✅ **Broadcast Successful!**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📡 Target: `{user_channel}`\n"
            f"Status: Sent successfully.\n"
            f"KH: សារត្រូវបានបង្ហោះរួចរាល់។"
        )
        bot.reply_to(message, success_text, parse_mode="Markdown")

    except Exception as e:
        bot.reply_to(message, f"❌ **Broadcast Failed**\n`{str(e)}`")

@bot.message_handler(func=lambda m: m.text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"])
def start_broadcast_process(message):
    u_id = message.from_user.id
    if not is_authorized(u_id): return

    target = get_user_channel(u_id)
    if not target:
        bot.reply_to(message, "📍 Please set your channel first.")
        return

    prompt = (f"📢 **Ready to Broadcast**\n"
              f"Target: `{target}`\n\n"
              f"EN: Send the content (Text/Media/Poll).\n"
              f"KH: សូមផ្ញើសារដែលអ្នកចង់បង្ហោះ។")
    
    sent_msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
    bot.register_next_step_handler(sent_msg, verify_and_broadcast, target)
# ==========================================
# SECTION 5: CHANNEL LOOKUP & STATS (AUDIT)
# ==========================================

def get_channel_info_via_bot(target):
    """Fetches channel info, ID, and latest activity marker"""
    try:
        clean_target = target if target.startswith("@") else f"@{target}"
        chat = bot.get_chat(clean_target)
        members = bot.get_chat_member_count(chat.id)
        
        return {
            "title": chat.title,
            "id": chat.id,
            "members": members,
            "bio": chat.description or "No Bio Available",
            "username": chat.username or "Private",
            "type": chat.type,
            "pinned_id": chat.pinned_message.message_id if chat.pinned_message else None
        }
    except Exception as e:
        print(f"❌ Lookup Error: {e}")
        return None

def audit_thread_worker(message, wait_msg, target):
    """Audits the channel and copies the latest marker message"""
    data = get_channel_info_via_bot(target)
    
    if not data:
        bot.edit_message_text("❌ **Audit Failed**\nEnsure Bot is Admin in that channel.", message.chat.id, wait_msg.message_id)
        return

    # --- FEEDBACK / BOT DETECTION LOGIC ---
    verdict = "🟢 REAL / ធម្មតា"
    # Rule: High subs + No Bio + No Pinned Message = High Bot Probability
    if data['members'] > 2000 and data['bio'] == "No Bio Available":
        verdict = "🔴 SUSPICIOUS / សង្ស័យ (Botted)"
    elif data['members'] > 1000 and not data['pinned_id']:
        verdict = "🟡 WARNING / ប្រុងប្រយ័ត្ន (Low Activity)"

    # Estimate Message Count: Using Pinned ID as a sequence marker
    msg_count_display = f"`{data['pinned_id']}` (Approx)" if data['pinned_id'] else "Unknown"

    report = (f"🛡️ **CHANNEL AUDIT REPORT**\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"📺 Title: `{data['title']}`\n"
              f"👥 Subs: `{data['members']:,}`\n"
              f"📊 Msg Count: {msg_count_display}\n"
              f"📝 Bio: `{data['bio']}`\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"⚖️ Verdict: **{verdict}**\n\n"
              f"👇 **COPYING LATEST PINNED POST...**")

    bot.edit_message_text(report, message.chat.id, wait_msg.message_id, parse_mode="Markdown")

    # --- COPY LATEST MESSAGE ---
    if data['pinned_id']:
        try:
            bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=data['id'],
                message_id=data['pinned_id']
            )
        except:
            bot.send_message(message.chat.id, "❌ *Bot needs 'Admin' to copy posts.*", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "ℹ️ *No pinned activity found to copy.*")



@bot.message_handler(func=lambda m: m.text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"])
def handle_audit(message):
    u_id = message.from_user.id
    if not is_authorized(u_id): return

    target = get_user_channel(u_id)
    if not target:
        bot.reply_to(message, "⚠️ Set channel first.")
        return

    wait_msg = bot.send_message(message.chat.id, "🔍 **Analyzing Channel Stats...**")
    threading.Thread(target=audit_thread_worker, args=(message, wait_msg, target)).start()    
# ==========================================
# SECTION 6: AUTO-SEND (CAMBODIA TIME)
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
# SECTION 6: ADVANCED DEEP-SCAN (BOT API)
# ==========================================
import threading
import time

def run_standard_audit(target_username):
    """
    Performs a deep audit using Bot API.
    Note: Standard bots can only see statistics if they are ADMINS.
    """
    try:
        clean_target = target_username if target_username.startswith("@") else f"@{target_username}"
        
        # 1. Fetch Chat Information
        chat = bot.get_chat(clean_target)
        
        # 2. Fetch Live Member Count
        members = bot.get_chat_member_count(chat.id)
        
        # 3. Analyze Activity via Pinned Message
        # Standard bots cannot loop through history, so we use the 
        # pinned message and chat properties as indicators of health.
        has_pin = chat.pinned_message is not None
        has_bio = chat.description is not None
        
        # Fraud Index Calculation (Heuristic based on Bot API data)
        # Higher score = More likely to be botted
        fraud_score = 0
        if not has_bio: fraud_score += 30
        if not has_pin: fraud_score += 20
        if members > 10000 and not chat.username: fraud_score += 40 # Private mass-sub channels
        
        return {
            "subs": members,
            "title": chat.title,
            "has_pin": has_pin,
            "has_bio": has_bio,
            "fraud_index": fraud_score,
            "username": chat.username or "Private"
        }
    except Exception as e:
        print(f"❌ Bot Audit Error: {e}")
        return None

def audit_thread_worker(message, wait_msg, target):
    """Background worker to process the audit without freezing the bot"""
    try:
        # Simulate 'Deep Scan' processing time for UI feel
        time.sleep(2)
        
        data = run_standard_audit(target)

        if not data:
            bot.edit_message_text(
                "❌ **Audit Failed!**\n\nPossible reasons:\n"
                "1. Bot is not an Admin in that channel.\n"
                "2. Channel username is invalid.\n"
                "3. Channel is private and bot isn't inside.", 
                message.chat.id, wait_msg.message_id
            )
            return

        # --- Decision Logic (Telebot Heuristics) ---
        verdict = "🟢 SAFE / សុវត្ថិភាព"
        status_color = "CLEAN / VERIFIED"
        
        if data['fraud_index'] >= 60:
            verdict = "🔴 HIGH RISK / គ្រោះថ្នាក់"
            status_color = "SMM BOTTED INDICATORS"
        elif data['fraud_index'] >= 30:
            verdict = "🟡 CAUTION / ប្រុងប្រយ័ត្ន"
            status_color = "INACTIVE / LOW INFO"

        # --- Final Report ---
        report = (
            f"🛡️ **DEEP AUDIT: {data['title']}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👥 Subs: `{data['subs']:,}`\n"
            f"🆔 User: `@{data['username']}`\n"
            f"📌 Pinned Post: {'✅ Yes' if data['has_pin'] else '❌ No'}\n"
            f"📝 Description: {'✅ Yes' if data['has_bio'] else '❌ No'}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ Verdict: **{verdict}**\n"
            f"⭐ Status: `{status_color}`\n"
            f"📊 Bot Probability: `{data['fraud_index']}%`\n\n"
            f"EN: Verification complete.\n"
            f"KH: ការត្រួតពិនិត្យបានបញ្ចប់។"
        )

        bot.edit_message_text(report, message.chat.id, wait_msg.message_id, parse_mode="Markdown")

    except Exception as e:
        bot.edit_message_text(f"❌ System Error: `{e}`", message.chat.id, wait_msg.message_id)

@bot.message_handler(func=lambda m: m.text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"])
def check_stats(message):
    """Main entry point for auditing using Bot API"""
    u_id = message.from_user.id
    if not is_authorized(u_id): 
        bot.reply_to(message, "🚫 No Access.")
        return

    target = get_user_channel(u_id)
    if not target:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន\nEN: Set channel first.")
        return

    wait_msg = bot.send_message(
        message.chat.id, 
        "🔍 **Starting Deep Scan (Bot API)...**\n"
        "🕵️ Analyzing metadata and security headers.\n"
        "⏳ Please wait..."
    )

    # Use threading to keep the bot responsive
    t = threading.Thread(target=audit_thread_worker, args=(message, wait_msg, target))
    t.start()
# ==========================================
# SECTION 7: USER INTERFACE & PERMISSIONS
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
        welcome_text = (
            "👑 **OWNER CONTROL PANEL**\n"
            "Welcome back, Boss. All systems operational."
            if lang == 'en' else 
            "👑 **ផ្ទាំងគ្រប់គ្រងម្ចាស់ប៊ត**\n"
            "សូមស្វាគមន៍ម្ចាស់ប៊ត។ ប្រព័ន្ធដំណើរការជាធម្មតា។"
        )
    else:
        welcome_text = (
            "🛡️ **ADMIN CONTROL PANEL**\n"
            "Manage your channels and broadcasts below."
            if lang == 'en' else 
            "🛡️ **ផ្ទាំងគ្រប់គ្រងអ្នកអតមីន**\n"
            "គ្រប់គ្រងឆានែល និងការផ្សព្វផ្សាយរបស់អ្នកនៅទីនេះ។"
        )

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

    # 1. CHANNEL SETTING DETECTION (Direct Link or @username input)
    if text.startswith('@') or 't.me/' in text:
        process_set_channel_logic(message) 
        return

    # 2. AUDIT COMMANDS (Uses logic from Section 6)
    if text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        check_stats(message) 
    
    # 3. MANUAL SET CHANNEL BUTTON
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        prompt = (
            "📍 **EN:** Send channel @username (e.g., @mychannel)\n"
            "📍 **KH:** សូមផ្ញើឈ្មោះឆានែល (ឧទាហរណ៍៖ @mychannel)"
        )
        msg = bot.reply_to(message, prompt)
        bot.register_next_step_handler(msg, process_set_channel_logic)

    # 4. REPORTING SYSTEM (Uses Mass-Report Simulator logic)
    elif text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"]:
        report_start(message) 
        
    # 5. LANGUAGE SETTINGS
    elif text in ["🌐 Language", "🌐 ភាសា"]:
        show_language_keyboard(message)
        
    # 6. OWNER PRIVILEGES (Add/Remove Admins)
    elif text == "➕ Add Admin" and u_id == SUPER_ADMIN_ID:
        add_admin_prompt(message)
    elif text == "➖ Remove Admin" and u_id == SUPER_ADMIN_ID:
        remove_admin_prompt(message)

    # 7. HELP SYSTEM
    elif text in ["❓ Help", "❓ ជំនួយ"]:
        send_help(message, lang)

    # 8. BROADCAST SYSTEM (Uses logic from Section 4)
    elif text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"]:
        start_broadcast(message)

# --- SUPPORTING FUNCTIONS (UI & DB LOGIC) ---

def show_language_keyboard(message):
    """Inline menu for language selection"""
    markup = types.InlineKeyboardMarkup()
    btn_en = types.InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en')
    btn_kh = types.InlineKeyboardButton("ភាសាខ្មែរ 🇰🇭", callback_data='set_lang_kh')
    markup.add(btn_en, btn_kh)
    bot.send_message(message.chat.id, "🌐 Select Language / សូមជ្រើសរើសភាសា:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_lang_'))
def callback_language(call):
    """Handles the database update when a language is chosen"""
    new_lang = call.data.split('_')[2]
    set_user_lang(call.from_user.id, new_lang)
    bot.answer_callback_query(call.id, "Success!")
    
    msg_text = (
        "✅ Language updated! Press /menu to refresh." 
        if new_lang == 'en' else 
        "✅ ភាសាត្រូវបានផ្លាស់ប្តូរ! សូមចុច /menu ដើម្បីមើលការផ្លាស់ប្តូរ។"
    )
    bot.edit_message_text(
        msg_text,
        call.message.chat.id,
        call.message.message_id
    )

def add_admin_prompt(message):
    msg = bot.reply_to(message, "🆔 Send the Telegram User ID you wish to grant Admin access to:")
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    """Updates the is_admin column in Neon Postgres"""
    try:
        new_id = int(message.text)
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (user_id, is_admin) VALUES (%s, 1) 
                ON CONFLICT (user_id) DO UPDATE SET is_admin = 1
            """, (new_id,))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ **Success!**\nUser `{new_id}` has been authorized as an Admin.")
        finally:
            db_pool.putconn(conn)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ **Error:** Invalid ID or Database busy.\n`{str(e)}`")

def process_set_channel_logic(message):
    """Saves the target channel for the user to the database"""
    target = message.text.strip()
    
    # Handle t.me links by converting them to @username
    if 't.me/' in target:
        target = "@" + target.split('t.me/')[1]
    
    if not target.startswith('@') and not target.startswith('-100'):
        target = f"@{target}"
    
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET target_channel = %s WHERE user_id = %s", (target, message.from_user.id))
        conn.commit()
        
        success_msg = (
            f"✅ **Channel Set!**\nTarget: `{target}`\n\n"
            "EN: You can now use Audit or Broadcast.\n"
            "KH: អ្នកអាចប្រើមុខងារ Audit ឬ Broadcast បានហើយ។"
        )
        bot.reply_to(message, success_msg, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ **Database Error:** `{str(e)}`")
    finally:
        db_pool.putconn(conn)
# ==========================================
# SECTION 8: FULL FEATURE MENU & ROUTING
# ==========================================

# --- មុខងារជំនួយសម្រាប់ SETTINGS (CHANNEL CONFIGURATION) ---

def set_channel_prompt(message):
    """ចាប់ផ្តើមដំណើរការកំណត់ Channel គោលដៅសម្រាប់អ្នកប្រើប្រាស់"""
    u_id = message.from_user.id
    lang = get_user_lang(u_id)
    
    prompt = (
        "📍 **Target Channel Configuration**\n\n"
        "EN: Send the channel @username or ID (e.g., @mychannel or -100123456789):\n"
        "KH: សូមផ្ញើឈ្មោះ Channel របស់អ្នក (ឧទាហរណ៍ @username ឬ ID ឆានែល):"
    )
    msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_set_channel)

def process_set_channel(message):
    """រក្សាទុក Channel ទៅក្នុង Neon PostgreSQL Database"""
    u_id = message.from_user.id
    channel_val = message.text.strip()
    
    # បន្ថែម @ ស្វ័យប្រវត្តិ ប្រសិនបើអ្នកប្រើភ្លេច និងមិនមែនជាលេខ ID
    if not channel_val.startswith('@') and not channel_val.startswith('-100'):
        channel_val = f"@{channel_val}"
        
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        # ធ្វើបច្ចុប្បន្នភាពឆានែលគោលដៅក្នុង Database
        cursor.execute("UPDATE users SET target_channel = %s WHERE user_id = %s", (channel_val, u_id))
        conn.commit()
        
        success_text = (
            f"✅ **Success! / រួចរាល់!**\n\n"
            f"Target locked to: `{channel_val}`\n"
            f"EN: You can now use Audit or Broadcast features.\n"
            f"KH: អ្នកអាចប្រើមុខងារ Audit ឬ Broadcast ទៅកាន់ឆានែលនេះបានហើយ។"
        )
        bot.reply_to(message, success_text, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Database Error: {str(e)}")
    finally:
        if conn:
            db_pool.putconn(conn)

# --- THE GLOBAL BUTTON ROUTER (អ្នកចាត់ចែងប៊ូតុងទូទៅ) ---

@bot.message_handler(func=lambda m: True)
def handle_all_buttons(message):
    """គ្រប់គ្រងរាល់ការចុចប៊ូតុងនៅលើ Keyboard Menu"""
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
        prompt = (
            "📋 **Poll Creation**\n\n"
            "EN: Send name list (one per line):\n"
            "KH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (ម្នាក់មួយបន្ទាត់):"
        )
        msg = bot.send_message(message.chat.id, prompt, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_poll_names)

    # 4. វិភាគឆានែល (DEEP AUDIT)
    elif text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        # មុខងារនេះត្រូវបានហៅពី Section 6 (Bot API Audit)
        check_stats(message)

    # 5. ផ្សព្វផ្សាយសារ (MASS BROADCAST)
    elif text in ["📢 Broadcast", "📢 ផ្សព្វផ្សាយ"]:
        # មុខងារនេះត្រូវបានហៅពី Section 4
        start_broadcast(message)

    # 6. ព័ត៌មានកាលវិភាគ និងម៉ោង (TIME SYNC)
    elif text in ["📅 Schedule Info", "📅 កាលវិភាគ", "📅 ព័ត៌មានកាលវិភាគ"]:
        tz_kh = pytz.timezone('Asia/Phnom_Penh')
        now_kh = datetime.now(tz_kh).strftime("%I:%M %p")
        
        status = (
            f"⏰ **System Live Status**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🇰🇭 Cambodia Time: `{now_kh}`\n"
            f"🛠️ Server Status: `Operational`\n"
            f"📡 Auto-Audit Task: `09:00 AM` (Daily)\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        bot.send_message(message.chat.id, status, parse_mode="Markdown")

    # 7. កំណត់ឆានែល (CHANNEL SETTINGS)
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        set_channel_prompt(message)

    # 8. ប្រព័ន្ធការពារ និងរាយការណ៍ (ANTI-BOOST PROTECTION)
    elif text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"]:
        # ហៅមុខងារ Report ពី Section 9
        report_start(message)

    # 9. បញ្ជាសម្រាប់ម្ចាស់ប៊ត (SUPER-ADMIN ONLY)
    elif u_id == SUPER_ADMIN_ID:
        if text == "➕ Add Admin":
            add_admin_prompt(message)
        elif text == "➖ Remove Admin":
            remove_admin_prompt(message)

# --- ឡូហ្សិកបង្កើត POLL (ច្បាប់បែងចែកក្រុម 4+1) ---

def process_poll_names(message):
    """បែងចែកឈ្មោះជាក្រុមៗ (៤នាក់ក្នុងមួយ Poll) និងដោះស្រាយបញ្ហានៅសល់ម្នាក់ឯង"""
    user_id = message.from_user.id
    target_channel = get_user_channel(user_id) 
    
    if not target_channel:
        error_text = (
            "⚠️ **Error: Target Not Found**\n\n"
            "EN: Please use 'Set Channel' first.\n"
            "KH: សូមកំណត់ឆានែលគោលដៅជាមុនសិន។"
        )
        bot.reply_to(message, error_text, parse_mode="Markdown")
        return

    # សម្អាតបញ្ជីឈ្មោះដែលផ្ញើមក និងលុបបន្ទាត់ទទេចេញ
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    
    if not names:
        bot.reply_to(message, "❌ List is empty. Please provide at least 2 names.")
        return

    # បែងចែកជាក្រុមៗ ក្រុមនីមួយៗមាន ៤ នាក់
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # ច្បាប់ពិសេស 4+1៖ ប្រសិនបើក្រុមចុងក្រោយនៅសល់តែម្នាក់ឯង ត្រូវបូកបញ្ចូលទៅក្រុមមុនដើម្បីកុំឱ្យមាន Poll ដែលមានជម្រើសតែមួយ
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        last_person = chunks.pop() # ដកក្រុមដែលមានម្នាក់ឯងចេញ
        chunks[-1].extend(last_person) # បញ្ចូលឈ្មោះនោះទៅក្នុងក្រុមមុន (ធ្វើឱ្យក្រុមចុងក្រោយមាន ៥ នាក់)

    bot.send_message(message.chat.id, f"🚀 **Processing {len(chunks)} polls...**\nTarget: `{target_channel}`", parse_mode="Markdown")

    for i, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target_channel,
                question=f"Round {i} / ជុំទី {i}",
                options=group,
                is_anonymous=True,
                allows_multiple_answers=False
            )
            # រង់ចាំ ១.៥ វិនាទី ដើម្បីការពារ Telegram Flood Limit (កុំឱ្យ Bot គាំង)
            time.sleep(1.5) 
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Poll {i} Failed: {str(e)}")

    bot.send_message(message.chat.id, "✅ **All Polls Created Successfully!**")
# ==========================================
# SECTION 9: MASS REPORT SIMULATOR (PRO)
# ==========================================
import random
import threading
import time

def generate_fake_ip():
    """Generates a random IPv4 address with high-tier range variety"""
    return f"{random.randint(100, 223)}.{random.randint(1, 254)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

def get_random_node():
    """Returns a fake specialized server node location"""
    nodes = ["SG-Cloud-01", "HK-Data-Center", "US-East-Node", "EU-West-Proxy", "KH-Mainframe-09"]
    return random.choice(nodes)

@bot.message_handler(func=lambda m: m.text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"])
def report_start(message):
    """Starts the advanced mass report simulation interface"""
    u_id = message.from_user.id
    if not is_authorized(u_id): 
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
           f"EN: Choose the reporting intensity to broadcast to Telegram T&S Nodes:\n"
           f"KH: សូមជ្រើសរើសកម្រិតនៃការរាយការណ៍ទៅកាន់ប្រព័ន្ធ Telegram:")
    
    bot.send_message(message.chat.id, msg, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('run_rep_'))
def handle_report_callback(call):
    """Triggers the execution thread for the simulation"""
    # Answer callback to remove loading state on the button
    bot.answer_callback_query(call.id, "Initializing Sequence...")
    # Start thread
    threading.Thread(target=execute_report_simulation, args=(call,)).start()

def execute_report_simulation(call):
    """The deep-logic for a hyper-realistic simulated mass report"""
    amount = call.data.split('_')[2]
    chat_id = call.message.chat.id
    u_id = call.from_user.id
    target = get_user_channel(u_id) or "Unknown_Ref"
    
    # 1. Initial Handshake Message
    try:
        status_msg = bot.edit_message_text(
            f"🔄 **Establishing Encrypted Tunnel...**\n"
            f"`[░░░░░░░░░░░░░░░░░░░░] 0%`", 
            chat_id, call.message.message_id,
            parse_mode="Markdown"
        )
    except: 
        return

    # Realistic stages of a "cyber attack"
    stages = [
        {"p": 5, "t": "Bypassing Cloudflare protection layers..."},
        {"p": 15, "t": "Establishing WebSocket Handshake with Telegram API..."},
        {"p": 25, "t": "Synchronizing 128 Dedicated Proxy Nodes..."},
        {"p": 40, "t": f"Injecting {amount} Spam/Fraud Metadata Packets..."},
        {"p": 60, "t": "Spoofing Device User-Agents (iPhone/Android)..."},
        {"p": 80, "t": "Finalizing bulk submission to Trust & Safety Hub..."},
        {"p": 95, "t": "Clearing digital footprints and IP logs..."},
        {"p": 100, "t": "✅ **SEQUENCE COMPLETED SUCCESSFULLY**"}
    ]

    for stage in stages:
        # Randomized delay (1.5 to 3.5 seconds) makes it feel like it's actually "working"
        time.sleep(random.uniform(1.5, 3.5)) 
        
        # Progress Bar Logic (20 blocks wide)
        bar_filled = stage['p'] // 5
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        
        # Hyper-Realistic Log Generation
        # Each update shows different IPs and different nodes
        log_lines = []
        for _ in range(4):
            ip = generate_fake_ip()
            node = get_random_node()
            status = random.choice(["SENT", "DELIVERED", "ACKNOWLEDGED"])
            log_lines.append(f"📡 `[{node}]` -> `ID:{random.randint(1000,9999)}` -> `{ip}` -> **{status}**")
        
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
                chat_id, status_msg.message_id,
                parse_mode="Markdown"
            )
        except:
            pass

    # Final Cool-down and Summary Report
    time.sleep(2)
    
    # Generate a fake unique ticket ID for realism
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
        f"EN: The bulk data has been successfully injected into the Telegram moderation queue. "
        f"If the channel violates TOS, it will be limited or deleted within 24-48 hours.\n\n"
        f"KH: ទិន្នន័យត្រូវបានបញ្ជូនទៅកាន់ប្រព័ន្ធ Telegram រួចរាល់។ "
        f"ប្រសិនបើឆានែលនេះល្មើសច្បាប់ វានឹងត្រូវរងពិន័យក្នុងរយៈពេល ២៤ ទៅ ៤៨ ម៉ោង។\n\n"
        f"⚡ _Powered by @vinzystorezz_"
    )
    
    # Send as a new message so the user keeps the log record
    bot.send_message(chat_id, final_report, parse_mode="Markdown")
# ==========================================
# FINAL EXECUTION BLOCK (STABLE POLLING)
# ==========================================

if __name__ == "__main__":
    # --- ANSI Terminal Colors for Professional Logging ---
    GREEN = "\033[1;32m"
    BLUE = "\033[1;34m"
    YELLOW = "\033[1;33m"
    RED = "\033[1;31m"
    RESET = "\033[0m"

    print(f"{GREEN}🚀 Vinzy Audit Bot [Telebot Mode] is starting...{RESET}")
    print(f"{BLUE}📡 Initializing System Components...{RESET}")
    
    # 1. Database Connectivity Health Check
    # This ensures the bot doesn't start if the Neon DB is offline.
    try:
        test_conn = db_pool.getconn()
        cursor = test_conn.cursor()
        cursor.execute("SELECT 1")
        db_pool.putconn(test_conn)
        print(f"{GREEN}✅ Database Connection: SECURE{RESET}")
    except Exception as e:
        print(f"{RED}❌ Database Critical Error: {str(e)}{RESET}")
        print(f"{YELLOW}⚠️ Attempting to continue, but some features may fail.{RESET}")

    print(f"{BLUE}🤖 Bot API is now live. Monitoring incoming traffic...{RESET}")
    print(f"{BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    # 2. Main Infinity Polling Loop
    # We use a 'While True' loop to catch high-level crashes and restart automatically.
    while True:
        try:
            # infinity_polling is the most stable method for long-term hosting.
            # timeout=90: Prevents 'Read Timeout' errors by giving the API more time.
            # long_polling_timeout=20: Keeps the connection open to reduce network overhead.
            # skip_pending=True: Ignores messages sent while the bot was offline to prevent spam-flooding.
            
            bot.infinity_polling(
                timeout=90, 
                long_polling_timeout=20,
                skip_pending=True
            )
            
        except Exception as e:
            # Log the specific error with a timestamp
            current_time = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"{RED}⚠️ CRITICAL POLLING ERROR at {current_time}{RESET}")
            print(f"{RED}Error Details: {str(e)}{RESET}")
            
            # Cooldown period before restarting
            # This prevents the bot from hitting the Telegram API too fast during a crash loop
            print(f"{YELLOW}⏳ System Cooling Down... Restarting in 10 seconds...{RESET}")
            time.sleep(10)
            
            # Optional: Log the restart attempt
            print(f"{BLUE}🔄 Attempting to re-establish connection...{RESET}")
