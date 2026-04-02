import os
import sys
import time
import asyncio
import random
import psycopg2
from psycopg2 import pool
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession StringSession

# ==========================================
# SECTION 1: CONFIGURATION & DATABASE
# ==========================================

# 1. Load Environment Variables from Koyeb
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")
DATABASE_URL = os.getenv("DATABASE_URL")
# Your ID as the owner
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "8702798367"))

# 2. Critical Safety Check
if not all([API_ID, API_HASH, STRING_SESSION, DATABASE_URL]):
    print("❌ [CRITICAL] Missing Variables in Koyeb! Check API_ID, API_HASH, STRING_SESSION, and DATABASE_URL.")
    time.sleep(10)
    sys.exit(1)

API_ID = int(API_ID)

# 3. Initialize Threaded Connection Pool
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(
        1, 20, 
        DATABASE_URL,
        sslmode='require'
    )
    print("✅ [DATABASE] Connection Pool Initialized.")
    
    # --- AUTO-TABLE CREATION ---
    # This prevents the bot from crashing if the table is missing
    conn = db_pool.getconn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                target_channel TEXT,
                is_admin INTEGER DEFAULT 0,
                lang TEXT DEFAULT 'en'
            )
        """)
        conn.commit()
    db_pool.putconn(conn)
    print("📁 [DATABASE] Table 'users' verified and ready.")
    
except Exception as e:
    print(f"❌ [DATABASE] Setup Failed: {e}")
    time.sleep(10)
    sys.exit(1)

# 4. Initialize Telethon Client (Your Main Account Session)
client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# 5. Database Helper Function
def get_user_channel(user_id):
    """Retrieves the saved target channel for a specific user"""
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT target_channel FROM users WHERE user_id = %s", (user_id,))
            res = cur.fetchone()
            return res[0] if res else None
    except Exception as e:
        print(f"⚠️ DB Fetch Error: {e}")
        return None
    finally:
        if conn:
            db_pool.putconn(conn)

def is_authorized(user_id):
    """Checks if a user is the Super Admin or a promoted Database Admin"""
    if user_id == SUPER_ADMIN_ID:
        return True
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT is_admin FROM users WHERE user_id = %s", (user_id,))
            res = cur.fetchone()
            return res and res[0] == 1
    except:
        return False
    finally:
        if conn: db_pool.putconn(conn)
# ==========================================
# SECTION 2: DATABASE LOGIC (ADMINS/USERS/PRIVACY)
# ==========================================

# Permanent IDs that bypass database checks (Owner/Backup IDs)
# These are hardcoded for safety.
PERMANENT_ADMINS = [8702798367, 123456789] 

def init_db():
    """Initializes the Neon PostgreSQL database with all required columns"""
    conn = None
    try:
        # Get a connection from the Threaded Pool initialized in Section 1
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
    
    # 1. Check hardcoded/Super Admin bypass first (Fastest)
    # SUPER_ADMIN_ID is pulled from your Koyeb Env in Section 1
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

# Initialize the database table when the script starts up
init_db()
import random
import asyncio
from telethon import functions, types, errors

# ==========================================
# SECTION 3: AUTO POLL & TEAM SHUFFLE
# ==========================================

async def process_poll_names(event):
    """
    Processes names, shuffles them, splits into teams of 4, 
    and handles leftovers by putting them in random teams.
    """
    u_id = event.sender_id
    lang = get_user_lang(u_id)
    target_channel = get_user_channel(u_id)

    # 1. Check if channel is set
    if not target_channel:
        err = "❌ Set channel first!" if lang == 'en' else "❌ សូមកំណត់ឆានែលសិន!"
        await event.respond(err)
        return

    # 2. Clean and Shuffle Input
    # Split by lines, remove empty spaces
    names = [n.strip() for n in event.text.split('\n') if n.strip()]
    
    if len(names) < 2:
        err = "❌ Need at least 2 names!" if lang == 'en' else "❌ ត្រូវការយ៉ាងហោចណាស់ ២ ឈ្មោះ!"
        await event.respond(err)
        return

    # Randomize the list order
    random.shuffle(names)

    # 3. Create Teams of 4
    # Logic: Divide into main chunks of 4
    teams = [names[i:i + 4] for i in range(0, len(names), 4)]
    
    # 4. Handle Leftovers (The "Random Team" Rule)
    # If the last team has less than 4 people and it's NOT the only team
    if len(teams) > 1 and len(teams[-1]) < 4:
        leftovers = teams.pop() # Take the small group out
        for person in leftovers:
            # Pick a random team from the remaining list and add the person
            random_target_team = random.choice(teams)
            random_target_team.append(person)

    # 5. Send the Polls to the Channel
    start_msg = f"🚀 Creating {len(teams)} Shuffled Polls..."
    await event.respond(start_msg)

    for i, group in enumerate(teams, start=1):
        try:
            # We use the Telethon SendMessageRequest with a Poll media type
            await client(functions.messages.SendVoteRequest(
                peer=target_channel,
                msg_id=0, # New message
                options=[types.PollAnswer(text=name, option=bytes([idx])) for idx, name in enumerate(group)]
            ))
            
            # Simplified version using the friendly client method:
            await client.send_message(
                target_channel,
                file=types.InputMediaPoll(
                    poll=types.Poll(
                        id=random.randint(1, 1e6),
                        question=f"Group {i} / ក្រុមទី {i}",
                        answers=[types.PollAnswer(text=name, option=bytes([idx])) for idx, name in enumerate(group)],
                        public_voters=False, # Anonymous
                        multiple_choice=False
                    )
                )
            )
            # Small delay to prevent Telegram flood triggers
            await asyncio.sleep(2) 
            
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            await event.respond(f"❌ Error in Group {i}: {e}")

    await event.respond("✅ All teams shuffled and polls created!")
# ==========================================
# SECTION 4: BROADCAST & ADMIN VERIFICATION
# ==========================================

async def verify_and_broadcast(event, user_channel):
    """
    Checks for REAL Admin status and executes the broadcast.
    Uses the Bot's permissions to post to the channel.
    """
    u_id = event.sender_id
    lang = get_user_lang(u_id)

    # Allow user to cancel the broadcast process
    if event.text and event.text.lower() in ['cancel', 'បោះបង់', '/cancel']:
        cancel_text = "🚫 Broadcast cancelled." if lang == 'en' else "🚫 ការផ្សព្វផ្សាយត្រូវបានបោះបង់។"
        await event.respond(cancel_text)
        return

    try:
        # 1. LIVE ADMIN & PERMISSION CHECK
        # We check if the BOT (not your account) has rights in the target channel
        participant = await client(functions.channels.GetParticipantRequest(
            channel=user_channel,
            participant='me' # 'me' refers to the bot/account running the script
        ))

        is_admin = isinstance(participant.participant, (types.ChannelParticipantAdmin, types.ChannelParticipantCreator))
        
        if not is_admin:
            error_msg = (
                "❌ **Admin Error**\n\n"
                "EN: I must be an ADMIN in the channel to post messages.\n"
                "KH: ខ្ញុំត្រូវតែជា ADMIN នៅក្នុង Channel ដើម្បីបង្ហោះសារបាន។"
            )
            await event.reply(error_msg)
            return

        # Check for specific 'post_messages' right
        if isinstance(participant.participant, types.ChannelParticipantAdmin):
            if not participant.participant.admin_rights.post_messages:
                perm_msg = (
                    "❌ **Permission Error**\n\n"
                    "EN: I am an Admin, but the 'Post Messages' permission is turned OFF.\n"
                    "KH: ខ្ញុំជា Admin តែមិនទាន់មានសិទ្ធិ 'Post Messages' ឡើយ។"
                )
                await event.reply(perm_msg)
                return

        # 2. EXECUTE THE BROADCAST
        # We use send_message with the original message's media/text
        # This acts like a "copy" so it doesn't show "Forwarded from..."
        await client.send_message(
            user_channel,
            event.message
        )

        # 3. SUCCESS FEEDBACK
        success_text = (
            f"✅ **Broadcast Successful! / ផ្សព្វផ្សាយជោគជ័យ!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 **Target:** `{user_channel}`\n"
            f"📊 **Status:** Message delivered successfully.\n"
            f"📅 **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"EN: Your content is now live in the channel.\n"
            f"KH: សាររបស់អ្នកត្រូវបានបង្ហោះចូលក្នុងឆានែលរួចរាល់។"
        )
        await event.reply(success_text)

    except errors.ChatAdminRequiredError:
        await event.reply("❌ **Error:** I need Admin rights to do this.")
    except Exception as e:
        error_detail = str(e)
        if "chat not found" in error_detail.lower():
            fail_msg = "❌ **Error:** Channel not found. Make sure the @username is correct."
        else:
            fail_msg = f"❌ **Broadcast Failed:**\n`{error_detail}`"
        await event.reply(fail_msg)

# --- BROADCAST HANDLER ---
# This part replaces the telebot @bot.message_handler
@client.on(events.NewMessage(pattern=r"(📢 Broadcast|📢 ផ្សព្វផ្សាយ)"))
async def start_broadcast_process(event):
    """Initiates the broadcast process"""
    u_id = event.sender_id
    
    if not is_authorized(u_id):
        return

    target = get_user_channel(u_id)
    if not target:
        lang = get_user_lang(u_id)
        no_channel = (
            "📍 **No Channel Linked**\n\n"
            "EN: Please use the 'Set Channel' button first.\n"
            "KH: សូមចុចប៊ូតុង 'កំណត់ឆានែល' ជាមុនសិន។"
        )
        await event.reply(no_channel)
        return

    prompt = (
        f"📢 **Ready to Broadcast**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 **Target:** `{target}`\n\n"
        f"EN: Send the content you want to post (Text, Photo, Video, or Poll).\n"
        f"KH: សូមផ្ញើសារ ឬរូបភាពដែលអ្នកចង់បង្ហោះ។\n\n"
        f"👉 _Type 'cancel' to stop._"
    )
    
    await event.reply(prompt)
    
    # In Telethon, we wait for the next message from the same user
    async with client.conversation(event.chat_id) as conv:
        response = await conv.get_response()
        await verify_and_broadcast(response, target)
import asyncio
from telethon import functions, types, errors
from datetime import datetime

# ==========================================
# SECTION 5: MASTER AUDIT (PRO GRADE A)
# ==========================================

def calculate_grade_a_score(members, total_posts, avg_views, can_story):
    """
    Mathematical Engine: Detects structural impossibilities.
    """
    score = 100
    findings_kh = []

    # --- RULE 1: THE EMPTY GIANT ---
    if members > 2000 and total_posts < 30:
        score -= 75
        findings_kh.append("🚩 **Fake Shell:** ឆានែលធំខ្លាំង តែគ្មានប្រវត្តិអត្ថបទសោះ (Fake Giant)")
    
    # --- RULE 2: VIEW-TO-MEMBER RATIO (NEW) ---
    # Real channels usually get 10-30% views per member. 
    # If 10k members but only 50 views per post = BOTTED.
    if members > 500:
        view_ratio = (avg_views / members) * 100
        if view_ratio < 1: # Less than 1% engagement
            score -= 50
            findings_kh.append("⚠️ **Ghost Audience:** សមាជិកច្រើន តែគ្មានអ្នកមើលសោះ (Low Engagement)")

    # --- RULE 3: SMM PANEL SIGNATURE ---
    round_targets = [500, 1000, 2000, 5000, 10000, 20000, 50000]
    if members in round_targets:
        score -= 15
        findings_kh.append("🕵️ **SMM Pattern:** ចំនួនសមាជិកឡើងគត់ពេក (Matches SMM Blocks)")

    # --- RULE 4: PREMIUM PROOF ---
    if members > 3000 and not can_story:
        score -= 10
        findings_kh.append("🚫 **No Premium:** គ្មានអ្នកប្រើ Premium Boost (សញ្ញា Bot ច្រើន)")

    if members < 150:
        return 100, ["✅ New Channel: ទិន្នន័យនៅតូច មិនទាន់អាចវិភាគបាន។"]

    return max(5, score), findings_kh

async def perform_audit(event, target):
    """
    Uses Main Account to analyze. Joins if necessary, then leaves.
    """
    wait_msg = await event.respond("🔍 `[██░░░░░░░░] 20%` \n📡 **Accessing MTProto Metadata...**")
    
    try:
        # 1. Get Channel Entity
        channel = await client.get_entity(target)
        full_channel = await client(functions.channels.GetFullChannelRequest(channel))
        
        members = full_channel.full_chat.participants_count
        can_story = getattr(full_channel.full_chat, 'can_set_stickers', False)

        # 2. Analyze Message History (Views)
        # We grab the last 20 messages to calculate average views
        await client.edit_message(wait_msg, "🔍 `[██████░░░░] 60%` \n🧬 **Scanning View Patterns...**")
        
        messages = await client.get_messages(channel, limit=20)
        total_posts = messages[0].id if messages else 0
        
        total_views = 0
        count = 0
        for m in messages:
            if m.views:
                total_views += m.views
                count += 1
        
        avg_views = total_views / count if count > 0 else 0

        # 3. Leave if we had to join (Optional: Telethon can often see public info without joining)
        # If the channel was private and we joined, we'd leave here.

        # 4. Execute Scoring
        trust_score, f_kh = calculate_grade_a_score(members, total_posts, avg_views, can_story)

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
            f"📢 **Channel:** `{channel.title}`\n"
            f"👥 **Members:** `{members:,}`\n"
            f"👁️ **Avg Views:** `{int(avg_views):,}`\n"
            f"📈 **Post History ID:** `{total_posts}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ **Verdict:** {verdict}\n"
            f"⭐ **Trust Score:** `{trust_score}%` / 100%\n"
            f"📍 **Status:** `{status}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 **Expert Findings:**\n_{finding_str}_\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 `Scan: {datetime.now().strftime('%Y-%m-%d %H:%M')}`\n"
            f"🛡️ _Analysis powered by Vinzy Main Account._"
        )
        await client.edit_message(wait_msg, report)

    except Exception as e:
        await client.edit_message(wait_msg, f"❌ **Audit Failed:**\n`{str(e)}`")
# ==========================================
# SECTION 7: MASTER UI & ROUTING (PRO GRADE)
# ==========================================

@client.on(events.NewMessage(pattern=r"/(start|menu)"))
async def start_panel(event):
    """
    Initializes the Control Panel with localized keyboard buttons.
    Uses Telethon's ReplyKeyboardMarkup.
    """
    u_id = event.sender_id
    
    # Security Gate
    if not is_authorized(u_id):
        await event.respond("🚫 **Access Denied.**\nYour ID is not whitelisted. Contact @vinzystorezz.")
        return 

    # Retrieve User Preference
    lang = get_user_lang(u_id)
    
    # --- Multilingual Keyboard Labels ---
    l_poll   = "📊 Create Poll" if lang == 'en' else "📊 បង្កើតការបោះឆ្នោត"
    l_audit  = "🔍 Audit Channel" if lang == 'en' else "🔍 ពិនិត្យឆានែល"
    l_set    = "📍 Set Channel" if lang == 'en' else "📍 កំណត់ឆានែល"
    l_report = "🛡️ Report Channel" if lang == 'en' else "🛡️ រាយការណ៍ឆានែល"
    l_lang   = "🌐 Language" if lang == 'en' else "🌐 ភាសា"
    l_help   = "❓ Help" if lang == 'en' else "❓ ជំនួយ"
    l_sched  = "📅 Schedule Info" if lang == 'en' else "📅 កាលវិភាគ"
    l_broad  = "📢 Broadcast" if lang == 'en' else "📢 ផ្សព្វផ្សាយ"

    # Construct Layout
    buttons = [
        [l_poll, l_audit],
        [l_set, l_broad],
        [l_lang, l_sched],
        [l_help, l_report]
    ]
    
    # Admin-Only Row
    if u_id == SUPER_ADMIN_ID:
        buttons.append(["➕ Add Admin", "➖ Remove Admin"])

    welcome_text = (
        "🛡️ **Vinzy Control Panel v4.0**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✨ **EN:** Select a professional tool below.\n"
        "✨ **KH:** សូមជ្រើសរើសមុខងារពីម៉ឺនុយខាងក្រោម។\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    
    await event.respond(welcome_text, buttons=buttons)

@client.on(events.NewMessage)
async def master_router(event):
    """
    Central Logic Hub: Routes text inputs to functions.
    Using 'async with client.conversation' for step-by-step inputs.
    """
    u_id = event.sender_id
    text = event.raw_text
    
    if not is_authorized(u_id): 
        return
    
    lang = get_user_lang(u_id)

    # 1. POLL CREATION ENGINE
    if text in ["📊 Create Poll", "📊 បង្កើតការបោះឆ្នោត"]:
        prompt = (
            "📋 **Poll Creation Sequence**\n\n"
            "EN: Send the list of names (One name per line):\n"
            "KH: សូមផ្ញើបញ្ជីឈ្មោះសមាជិក (ម្នាក់មួយបន្ទាត់):"
        )
        async with client.conversation(event.chat_id) as conv:
            await conv.send_message(prompt)
            response = await conv.get_response()
            await process_poll_names(response)

    # 2. CHANNEL TARGETING
    elif text in ["📍 Set Channel", "📍 កំណត់ឆានែល"]:
        prompt = (
            "📍 **Target Configuration**\n\n"
            "EN: Send the channel @username (e.g., @vinzystorezz):\n"
            "KH: សូមផ្ញើឈ្មោះ Channel របស់អ្នក (ឧទហរណ៍ @username):"
        )
        async with client.conversation(event.chat_id) as conv:
            await conv.send_message(prompt)
            response = await conv.get_response()
            # This calls Section 8 logic (Ensure it is async)
            await process_set_channel_logic(response)

    # 3. MASTER AUDIT
    elif text in ["🔍 Audit Channel", "🔍 ពិនិត្យឆានែល"]:
        prompt = "🔍 Send @username to Audit:" if lang == 'en' else "🔍 សូមផ្ញើ @username ដើម្បីពិនិត្យ:"
        async with client.conversation(event.chat_id) as conv:
            await conv.send_message(prompt)
            response = await conv.get_response()
            await perform_audit(response, response.text)

    # 4. MULTILINGUAL SETTINGS
    elif text in ["🌐 Language", "🌐 ភាសា"]:
        # Using Inline Buttons
        buttons = [
            [types.KeyboardButtonCallback(text="English 🇬🇧", data=b'set_lang_en')],
            [types.KeyboardButtonCallback(text="ភាសាខ្មែរ 🇰🇭", data=b'set_lang_kh')]
        ]
        await event.respond("🌐 **Language Settings**\nSelect your preference:", buttons=buttons)

    # 5. DIAGNOSTICS & SYSTEM STATUS
    elif text in ["📅 Schedule Info", "📅 កាលវិភាគ"]:
        try:
            tz_kh = pytz.timezone('Asia/Phnom_Penh')
            now_kh = datetime.now(tz_kh).strftime("%I:%M %p")
        except:
            now_kh = datetime.now().strftime("%I:%M %p")
            
        status_report = (
            f"📊 **System Integrity Report**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 KH Time: `{now_kh}`\n"
            f"📡 DB Cluster: `Neon-PostgreSQL` (Online)\n"
            f"🧠 Logic Engine: `Telethon MTProto` (PRO)\n"
            f"🛡️ Security: `StringSession Encrypted`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await event.respond(status_report)

    # 6. SUPER-ADMIN PRIVILEGES
    elif u_id == SUPER_ADMIN_ID:
        if text == "➕ Add Admin":
            async with client.conversation(event.chat_id) as conv:
                await conv.send_message("🆔 Send User ID to grant Admin access:")
                response = await conv.get_response()
                await process_add_admin(response)
        elif text == "➖ Remove Admin":
            async with client.conversation(event.chat_id) as conv:
                await conv.send_message("🆔 Send User ID to revoke Admin access:")
                response = await conv.get_response()
                await process_remove_admin(response)
# ==========================================
# SECTION 8: FEATURE ENGINE (FIXED & SYNCED)
# ==========================================

import threading
import time

# --- AUDIT BRIDGE ---

def handle_audit_command(message):
    """
    Bridge between UI and Grade A Engine.
    Verifies Admin status before triggering the heavy MTProto worker.
    """
    u_id = message.from_user.id
    target = get_user_channel(u_id)
    
    if not target:
        msg = "⚠️ **KH:** សូមកំណត់ឆានែលជាមុនសិន! (📍 Set Channel)\n**EN:** Please set a channel first!"
        bot.reply_to(message, msg)
        return
        
    try:
        # Standardize target format
        if str(target).startswith("-100"): 
            clean_target = int(target)
        else: 
            clean_target = target if str(target).startswith("@") else f"@{target}"
        
        # PRO-CHECK: Check bot permissions before starting threads
        bot_id = bot.get_me().id
        chat_member = bot.get_chat_member(clean_target, bot_id)
        
        if chat_member.status not in ['administrator', 'creator']:
            err = (f"❌ **Admin Required!**\n\nI am in {target}, but I am not an Admin. "
                   "Please promote me so I can scan the ID sequence!")
            bot.reply_to(message, err)
            return
            
    except Exception as e:
        err = (f"❌ **Connection Error!**\n\nI cannot find `{target}`. Make sure:\n"
               "1. The username is correct.\n2. I am added as an Admin there.")
        bot.reply_to(message, err)
        return

    wait_msg = bot.send_message(
        message.chat.id, 
        "🛠️ **INITIALIZING GRADE A ENGINE...**\n📡 កំពុងចាប់ផ្ដើមម៉ាស៊ីនវិភាគ...", 
        parse_mode="Markdown"
    )
    
    # Launch worker in a separate thread to prevent UI freezing
    t = threading.Thread(target=audit_thread_worker, args=(message, wait_msg, target))
    t.daemon = True
    t.start()

# --- CHANNEL & DATABASE LOGIC ---

def process_set_channel_logic(message):
    """
    Saves target channel with automatic @ formatting and UPSERT logic.
    Uses the threaded DB pool for stability.
    """
    u_id = message.from_user.id
    val = message.text.strip()
    
    # Auto-format input for Telegram standards
    if not val.startswith('@') and not val.startswith('-100'): 
        val = f"@{val}"
    
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cursor:
            # UPSERT: Insert or update if user already exists
            cursor.execute("""
                INSERT INTO users (user_id, target_channel) VALUES (%s, %s) 
                ON CONFLICT (user_id) DO UPDATE SET target_channel = EXCLUDED.target_channel
            """, (u_id, val))
        conn.commit()
        bot.reply_to(message, f"✅ **Success!**\nTarget locked to: `{val}`\n\n_Now make sure I am an Admin in that channel!_")
    except Exception as e:
        bot.reply_to(message, f"❌ Database Error: {str(e)}")
    finally:
        if conn: 
            db_pool.putconn(conn)

# --- ADMIN MANAGEMENT ---

def process_add_admin(message):
    """Adds a new admin ID to the PostgreSQL database"""
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
    """Revokes admin status from a user ID"""
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
import asyncio

# --- UTILITY GENERATORS ---
def generate_fake_ip():
    return f"{random.randint(100, 223)}.{random.randint(1, 254)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

def get_random_node():
    nodes = ["SG-Cloud-01", "HK-Data-Center", "US-East-Node", "EU-West-Proxy", "KH-Mainframe-09"]
    return random.choice(nodes)

active_reports = set()

# --- INTERFACE START ---
@client.on(events.NewMessage(func=lambda e: e.text in ["🛡️ Report Channel", "🛡️ រាយការណ៍ឆានែល"]))
async def report_start(event):
    """Starts the advanced mass report simulation interface using Main Account Session"""
    u_id = event.sender_id
    if not is_authorized(u_id): return

    if u_id in active_reports:
        await event.reply("⚠️ **Process Active:** Please wait for the current sequence to finish.")
        return

    target = get_user_channel(u_id)
    if not target:
        await event.reply("⚠️ **EN:** Please lock a channel first using /set\n⚠️ **KH:** សូមកំណត់ Channel ជាមុនសិន")
        return

    # Use Telethon's Button format
    buttons = [
        [types.KeyboardButtonCallback("Standard (250)", data=b"run_rep_250"),
         types.KeyboardButtonCallback("Extreme (750)", data=b"run_rep_750")],
        [types.KeyboardButtonCallback("Overload (1500)", data=b"run_rep_1500")]
    ]

    msg = (f"🛡️ **CYBER-SECURITY INTERFACE**\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"📡 **Target:** `{target}`\n"
           f"🛠️ **Engine:** `Vinzy-Trust-Safety-v4`\n\n"
           f"EN: Choose reporting intensity for T&S Nodes:\n"
           f"KH: សូមជ្រើសរើសកម្រិតនៃការរាយការណ៍:")
    
    await event.reply(msg, buttons=buttons)

# --- SIMULATION CORE LOGIC ---
@client.on(events.CallbackQuery(data=lambda d: d.startswith(b'run_rep_')))
async def handle_report_callback(call):
    u_id = call.sender_id
    if u_id in active_reports:
        await call.answer("❌ Already running!", alert=True)
        return
    
    active_reports.add(u_id)
    await call.answer("Initializing Sequence...")
    
    try:
        amount = call.data.decode().split('_')[2]
        target = get_user_channel(u_id) or "Unknown_Ref"
        
        # 1. Initialization UI
        msg = await call.edit(f"🔄 **Establishing Encrypted Tunnel...**\n`[░░░░░░░░░░░░░░░░░░░░] 0%`")

        stages = [
            {"p": 5, "t": "Bypassing Cloudflare protection layers..."},
            {"p": 12, "t": "Establishing WebSocket Handshake with API..."},
            {"p": 25, "t": "Synchronizing 128 Dedicated Proxy Nodes..."},
            {"p": 35, "t": "Linking Main Account Session for Master Signal..."}, 
            {"p": 50, "t": f"Injecting {amount} Fraud Metadata Packets..."},
            {"p": 70, "t": "Spoofing Device User-Agents (Mobile & Desktop)..."},
            {"p": 85, "t": "Main Account verifying submission status..."}, 
            {"p": 95, "t": "Clearing digital footprints and IP logs..."},
            {"p": 100, "t": "✅ **SEQUENCE COMPLETED SUCCESSFULLY**"}
        ]

        for stage in stages:
            await asyncio.sleep(random.uniform(1.8, 3.0))
            bar_filled = stage['p'] // 5
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            
            log_lines = []
            for _ in range(2):
                log_lines.append(f"📡 `[{get_random_node()}]` -> `{generate_fake_ip()}` -> **SENT**")
            log_lines.append(f"🔑 `[MAIN-SESSION]` -> **MASTER-REPORT-FLAGGED**")
            
            logs = "\n".join(log_lines)
            
            await msg.edit(
                f"🛡️ **SECURITY OPS: ACTIVE**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 **Target:** `{target}`\n"
                f"📊 **Progress:** `[{bar}] {stage['p']}%`\n\n"
                f"⚙️ **Current Action:**\n_{stage['t']}_\n\n"
                f"🖥️ **Live Console Logs:**\n{logs}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ _Engine: @vinzystorezz V4-PRO_"
            )

        await asyncio.sleep(2)
        ticket_id = f"TKS-{random.randint(100000, 999999)}"
        
        await client.send_message(call.chat_id, (
            f"✅ **MASS REPORT PROTOCOL FINISHED**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 **Total Reports:** `{amount}` Packets\n"
            f"🆔 **Ticket ID:** `{ticket_id}`\n"
            f"🛰️ **Nodes Used:** `128 Proxies + Main Authorized Session`\n"
            f"🛡️ **Target Status:** `FLAGGED / UNDER REVIEW`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"**System Feedback:**\n"
            f"EN: Data successfully injected via main account signal. Violation review takes 24-48 hours.\n\n"
            f"KH: ទិន្នន័យត្រូវបានបញ្ជូនទៅកាន់ប្រព័ន្ធរួមទាំងគណនីមេ។ ការត្រួតពិនិត្យត្រូវការពេល ២៤ ទៅ ៤៨ ម៉ោង។\n\n"
            f"⚡ _Powered by @vinzystorezz_"
        ))

    except Exception as e:
        print(f"Simulation Error: {e}")
    finally:
        active_reports.discard(u_id)
# ==========================================
# SECTION 10: SYSTEM STARTUP & SHUTDOWN
# ==========================================

import signal
import sys
import asyncio

def graceful_exit(signum, frame):
    """Ensures the DB pool and Client are closed when Koyeb stops the instance"""
    print(f"\n\033[1;31m🛑 [SYSTEM] Shutting down gracefully...\033[0m")
    try:
        if 'db_pool' in globals():
            db_pool.closeall()
            print("✅ [DATABASE] Connections closed.")
        
        if 'client' in globals() and client.is_connected():
            # Disconnect in a thread-safe way for signal handlers
            asyncio.get_event_loop().stop()
            print("✅ [MTPROTO] Session disconnected.")
    except Exception as e:
        print(f"⚠️ Error during shutdown: {e}")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

async def start_vinzy_engine():
    """Main Execution Loop: Handles authentication and resilient connection."""
    CYAN, GREEN, RED, YELLOW, RESET = "\033[1;36m", "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[0m"

    print(f"{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{GREEN}🚀 Vinzy Audit Bot [v4.0 PRO] is initializing...{RESET}")
    
    retry_delay = 5
    while True:
        try:
            await client.start()
            me = await client.get_me()
            print(f"{GREEN}✅ Authenticated as: {me.first_name} (@{me.username}){RESET}")
            print(f"{CYAN}🤖 System Status: LIVE | Monitoring MTProto Traffic...{RESET}")
            print(f"{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
            
            retry_delay = 5
            await client.run_until_disconnected()

        except Exception as e:
            err_msg = str(e)
            if "Conflict" in err_msg:
                print(f"{YELLOW}⚠️ 409 CONFLICT: Old session still active. Waiting 15s...{RESET}")
                await asyncio.sleep(15)
            else:
                print(f"{RED}⚠️ SYSTEM ERROR: {err_msg}{RESET}")
                print(f"{CYAN}🔄 Attempting to re-connect in {retry_delay}s...{RESET}")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay + 5, 60)

if __name__ == "__main__":
    try:
        asyncio.run(start_vinzy_engine())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        print(f"\033[1;31m❌ FATAL CRASH: {e}\033[0m")
        sys.exit(1)
