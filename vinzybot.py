import telebot
from telebot import types
import sqlite3
import threading
import time
import pytz
from datetime import datetime

# ==========================================
# SECTION 1: CONFIGURATION
# ==========================================
BOT_TOKEN = "8782687814:AAEj5hYbo7a2TFZnfYWF7zf1NaCPx4fgyT0"
SUPER_ADMIN_ID = 8702798367  # Your ID
bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# SECTION 2: DATABASE LOGIC (Admins/Users/Privacy)
# ==========================================
def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    # Users table: stores who is allowed to use the bot and their specific channel
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, is_admin INTEGER, target_channel TEXT)''')
    conn.commit()
    conn.close()

def is_authorized(user_id):
    if user_id == SUPER_ADMIN_ID: return True
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

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
    """Fetch the specific channel locked to a user from the database"""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    # Ensure privacy: We only look for the channel belonging to THIS user_id
    c.execute("SELECT target_channel FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    # Returns the channel ID (e.g., "@vinzystorez") or None if not set
    if result and result[0]:
        return result[0]
    return None

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

    # 2. Privacy Check: Get ONLY their locked channel
    # This prevents Person A from broadcasting to Person B's channel
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
    user_id = message.from_user.id
    if not is_authorized(user_id): return

    target = get_user_channel(user_id)
    if not target:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន / EN: Set channel first.")
        return

    try:
        # 1. ADMIN & MEMBER COUNT CHECK
        chat = bot.get_chat(target)
        members_count = bot.get_chat_member_count(target)
        
        # Verify Admin Permissions for Log Scanning
        bot_member = bot.get_chat_member(target, bot.get_me().id)
        if bot_member.status != 'administrator':
            raise Exception("Missing Admin Status")

        # 2. SCAN FOR DELETED MESSAGES (Last 48 Hours)
        recent_deletes = 0
        try:
            logs = bot.get_chat_admin_log(chat.id, types=['message_delete'])
            recent_deletes = len(logs)
        except Exception:
            recent_deletes = -1 # Log access restricted

        # 3. FETCH DATA FROM LATEST POST (Pinned or Recent)
        last_post_views = 0
        last_post_forwards = 0
        if chat.pinned_message:
            # Note: Detailed view stats require the bot to be admin
            # These values are pulled from the message object
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

        # If it's a high-sub account with zero history/description
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

        # 4. FINAL REPORT
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
        msg = ("❌ **PERMISSIONS ERROR / ត្រូវការសិទ្ធិ Admin**\n\n"
               "EN: Add me as Admin with 'View Admin Logs' and 'Delete Messages' perms.\n"
               "KH: សូមដាក់ខ្ញុំជា Admin និងផ្ដល់សិទ្ធិ 'View Admin Logs' ដើម្បីវិភាគ។")
        bot.reply_to(message, msg)
# ==========================================
# SECTION 7: USER INTERFACE & PERMISSIONS
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def start(message):
    u_id = message.from_user.id
    
    # Check if they are authorized
    if not is_authorized(u_id):
        # KH/EN Sale Message
        msg = ("🚫 Access Denied!\n\n"
               "EN: This bot is private. Please pay to gain access.\n"
               "KH: គណនីរបស់អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។ សូមទិញសិទ្ធិប្រើប្រាស់ពីម្ចាស់ប៊ត។\n\n"
               "Features: Polls, Anti-Raid, Scheduling, Stats.")
        bot.send_message(message.chat.id, msg)
        return

    # Create the "4 Dots" Persistent Menu
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    if u_id == SUPER_ADMIN_ID:
        # Owner Buttons
        markup.add("📊 Create Poll", "🔍 Detect Botting")
        markup.add("📍 Set Channel", "📢 Broadcast")
        markup.add("➕ Add Admin", "➖ Remove Admin")
        bot.send_message(message.chat.id, "Welcome Creator. Control the system using the buttons below or the 4-dot menu.", reply_markup=markup)
    else:
        # Authorized User Buttons
        markup.add("📊 Create Poll", "🔍 Detect Botting")
        markup.add("📍 Set Channel")
        bot.send_message(message.chat.id, "Welcome Authorized User. Use the buttons below to begin.", reply_markup=markup)

@bot.message_handler(commands=['normal'])
def remove_keyboard(message):
    """Removes the persistent menu buttons and returns to standard text input"""
    markup = types.ReplyKeyboardRemove()
    bot.send_message(message.chat.id, "✅ Back to normal mode. Type /menu to show buttons again.", reply_markup=markup)

# --- TEXT BUTTON ROUTING ---
# This ensures clicking the "4-dot" menu buttons actually triggers the code
@bot.message_handler(func=lambda m: True)
def handle_menu_text(message):
    u_id = message.from_user.id
    if not is_authorized(u_id):
        return

    if message.text == "📊 Create Poll":
        prompt = (
            "📋 **AUTO POLL GENERATOR**\n\n"
            "EN: Please send the list of names (one name per line).\n"
            "KH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (មួយឈ្មោះក្នុងមួយបន្ទាត់):"
        )
        msg = bot.send_message(message.chat.id, prompt)
        bot.register_next_step_handler(msg, process_poll_names)

    elif message.text == "🔍 Detect Botting":
        check_stats(message)

    elif message.text == "📍 Set Channel":
        set_channel_prompt(message)

    elif message.text == "📢 Broadcast" and u_id == SUPER_ADMIN_ID:
        start_broadcast(message)

    elif message.text == "➕ Add Admin" and u_id == SUPER_ADMIN_ID:
        add_admin_prompt(message)

    elif message.text == "➖ Remove Admin" and u_id == SUPER_ADMIN_ID:
        remove_admin_prompt(message)

# --- ADMIN MANAGEMENT LOGIC ---

@bot.message_handler(func=lambda m: m.text == "Add Admin" and m.from_user.id == SUPER_ADMIN_ID)
def add_admin_prompt(message):
    msg = bot.reply_to(message, "Forward a message from the user or send their numerical Telegram ID:")
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    try:
        new_id = int(message.text)
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute('''INSERT INTO users (user_id, is_admin) VALUES(?, 1)
                     ON CONFLICT(user_id) DO UPDATE SET is_admin=1''', (new_id,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"✅ User {new_id} added successfully.")
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Invalid ID format. Please send numbers only.")

@bot.message_handler(func=lambda m: m.text == "Remove Admin" and m.from_user.id == SUPER_ADMIN_ID)
def remove_admin_prompt(message):
    msg = bot.reply_to(message, "Send the Telegram ID of the admin you want to remove:")
    bot.register_next_step_handler(msg, process_remove_admin)

def process_remove_admin(message):
    try:
        target_id = int(message.text)
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute("UPDATE users SET is_admin=0 WHERE user_id=?", (target_id,))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"✅ User {target_id} removed from admin list.")
    except Exception:
        bot.send_message(message.chat.id, "❌ Error: Could not find or remove user.")

# --- CHANNEL SETTING LOGIC ---

def set_channel_prompt(message):
    msg = bot.reply_to(message, "EN: Send your Channel ID (e.g., @yourchannel)\nKH: សូមផ្ញើ ID Channel របស់អ្នក (ឧទាហរណ៍: @yourchannel):")
    bot.register_next_step_handler(msg, process_set_channel)

def process_set_channel(message):
    try:
        channel_id = message.text.strip()
        if not (channel_id.startswith("@") or channel_id.startswith("-100")):
            bot.reply_to(message, "❌ Invalid format. Must start with @ or -100")
            return
            
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute('''INSERT INTO users (user_id, target_channel) VALUES(?, ?)
                     ON CONFLICT(user_id) DO UPDATE SET target_channel=excluded.target_channel''', 
                  (message.from_user.id, channel_id))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Target Channel set to: {channel_id}")
    except Exception as e:
        bot.reply_to(message, f"❌ Database error: {str(e)}")

# --- POLL GENERATION LOGIC ---

def process_poll_names(message):
    user_id = message.from_user.id
    target_channel = get_user_channel(user_id) 
    
    if not target_channel:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន | EN: Set channel first.")
        return

    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    if not names:
        bot.reply_to(message, "❌ KH: បញ្ជីឈ្មោះទទេរ! | EN: List is empty.")
        return

    # 4+1 Rule Grouping
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        leftover_person = chunks.pop() 
        chunks[-1].extend(leftover_person) 

    bot.send_message(message.chat.id, f"🚀 Creating {len(chunks)} polls in {target_channel}...")

    for index, group in enumerate(chunks, start=1):
        try:
            bot.send_poll(
                chat_id=target_channel,
                question=f"Poll {index}",
                options=group,
                is_anonymous=True # FIXED: Must be True for channels
            )
            time.sleep(1)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error in Poll {index}: {str(e)}")

    bot.send_message(message.chat.id, "✅ Done! All polls sent successfully.")
# ==========================================
# SECTION 8: FULL FEATURE MENU
# ==========================================

@bot.message_handler(commands=['menu', 'start'])
def show_main_menu(message):
    """Displays the persistent 4-dot grid menu with all features"""
    u_id = message.from_user.id
    if not is_authorized(u_id):
        # KH/EN Sale Message for unauthorized users
        msg = ("🚫 Access Denied!\n\n"
               "EN: This bot is private. Please pay to gain access.\n"
               "KH: គណនីរបស់អ្នកមិនមានសិទ្ធិប្រើប្រាស់ទេ។ សូមទិញសិទ្ធិប្រើប្រាស់ពីម្ចាស់ប៊ត។")
        bot.send_message(message.chat.id, msg)
        return
        
    # Using ReplyKeyboardMarkup for the persistent "4-dot" grid menu
    # resize_keyboard=True makes the buttons fit the screen neatly
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Core buttons available to both Owner and Admins
    btn1 = "📊 Create Poll"
    btn2 = "🔍 Audit Channel"
    btn3 = "📢 Broadcast"
    btn4 = "📅 Schedule Info"
    btn5 = "📍 Set Channel"
    btn6 = "🛡️ Poll Detection"
    
    # Building the grid layout
    markup.add(btn1, btn2)
    markup.add(btn3, btn4)
    markup.add(btn5, btn6)
    
    # If the user is the Creator, add User Management buttons at the bottom
    if u_id == SUPER_ADMIN_ID:
        markup.add("➕ Add Admin", "➖ Remove Admin")
        menu_text = "👑 **OWNER CONTROL PANEL**\nSelect a tool from the menu below:"
    else:
        menu_text = "🛡️ **ADMIN CONTROL PANEL**\nSelect a tool from the menu below:"

    bot.send_message(message.chat.id, menu_text, reply_markup=markup)

# --- TEXT BUTTON ROUTER ---
# This links the physical menu buttons to the specific code logic
@bot.message_handler(func=lambda m: True)
def handle_all_buttons(message):
    u_id = message.from_user.id
    if not is_authorized(u_id): 
        return

    # 1. CREATE POLL LOGIC
    if message.text == "📊 Create Poll":
        msg = bot.send_message(message.chat.id, "📋 EN: Send name list (one per line):\nKH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (មួយឈ្មោះក្នុងមួយបន្ទាត់):")
        bot.register_next_step_handler(msg, process_poll_names)

    # 2. AUDIT CHANNEL LOGIC (From Section 6)
    elif message.text == "🔍 Audit Channel":
        bot.send_message(message.chat.id, "🔎 EN: Running Channel Audit... | KH: កំពុងពិនិត្យ Channel...")
        check_stats(message)

    # 3. BROADCAST LOGIC (Now enabled for Admins to their specific channel)
    elif message.text == "📢 Broadcast":
        start_broadcast(message)

    # 4. SCHEDULE INFO LOGIC
    elif message.text == "📅 Schedule Info":
        # Check current Cambodia Time
        tz_kh = pytz.timezone('Asia/Phnom Penh')
        now_kh = datetime.now(tz_kh).strftime("%H:%M:%S")
        bot.send_message(message.chat.id, 
                         f"⏰ **Schedule System Status**\n\n"
                         f"Current Time (KH): {now_kh}\n"
                         f"Auto-Post Time: 09:00 AM\n"
                         f"Status: Active ✅\n\n"
                         f"Note: This system automatically syncs with Cambodia Time.")

    # 5. SET CHANNEL LOGIC
    elif message.text == "📍 Set Channel":
        set_channel_prompt(message)

    # 6. POLL DETECTION MONITOR (Visual UI for Section 3 logic)
    elif message.text == "🛡️ Poll Detection":
        bot.send_message(message.chat.id, 
                         "🕵️ **Anti-Boost Monitor Active**\n\n"
                         "The system is currently monitoring for:\n"
                         "• Abnormal voting speed\n"
                         "• SMM Drip-feed patterns\n"
                         "• Instant spikes (>15 votes/3s)\n\n"
                         "If botting is detected, an alert will be sent to you automatically.")

    # 7. OWNER ONLY: USER MANAGEMENT
    elif message.text == "➕ Add Admin" and u_id == SUPER_ADMIN_ID:
        add_admin_prompt(message)
    elif message.text == "➖ Remove Admin" and u_id == SUPER_ADMIN_ID:
        remove_admin_prompt(message)

# --- POLL PROCESSING FUNCTION ---

def process_poll_names(message):
    """Processes name list and creates polls with the '5th Person Overflow' rule"""
    user_id = message.from_user.id
    target_channel = get_user_channel(user_id) 
    
    if not target_channel:
        bot.reply_to(message, "⚠️ KH: សូមកំណត់ Channel ជាមុនសិន (/set_channel) | EN: Set channel first.")
        return

    # Clean and Parse the list
    names = [n.strip() for n in message.text.split('\n') if n.strip()]
    
    if not names:
        bot.reply_to(message, "❌ KH: បញ្ជីឈ្មោះទទេរ! | EN: List is empty.")
        return

    # Smart Grouping Logic (4+1 Rule)
    chunks = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # If the last poll has only 1 person, merge them into the previous poll
    if len(chunks) > 1 and len(chunks[-1]) == 1:
        leftover_person = chunks.pop() 
        chunks[-1].extend(leftover_person) 

    bot.send_message(message.chat.id, f"🚀 KH: កំពុងបង្កើត Poll ចំនួន {len(chunks)} ទៅកាន់ {target_channel}...")

    for index, group in enumerate(chunks, start=1):
        try:
            # IMPORTANT: is_anonymous MUST be True for channel polls (Telegram Rule)
            bot.send_poll(
                chat_id=target_channel,
                question=f"Poll {index}",
                options=group,
                is_anonymous=True 
            )
            time.sleep(1) # Rate limit safety
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
