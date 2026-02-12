import os
import threading
import random
import string
import urllib.parse
import urllib.request
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# ───────── DATABASE ───────── #
client = MongoClient(os.getenv("MONGO_URL"))
db = client["telegram_bot"]
users = db["users"]
rewards = db["rewards"]
withdraws = db["withdraws"]
giftcodes = db["giftcodes"]
settings = db["settings"]

ADMIN_CHANNEL = int(os.getenv("ADMIN_CHANNEL"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

# Ensure settings exist
settings.update_one({"_id": "withdraw"}, {"$setOnInsert": {"enabled": False}}, upsert=True)

# ───────── BOT ───────── #
Bot = Client(
    "EarnBot",
    bot_token=os.environ["BOT_TOKEN"],
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"]
)

# ───────── HELPERS ───────── #
def ensure_user(uid):
    if not users.find_one({"_id": uid}):
        users.insert_one({"_id": uid, "wallet": 0, "last_gen": None})

def withdraw_enabled():
    s = settings.find_one({"_id": "withdraw"})
    return s.get("enabled", False)

def set_withdraw(value: bool):
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": value}}, upsert=True)

def can_withdraw(uid, amount):
    if not withdraw_enabled():
        return False, "❌ Withdraw is OFF by admin."
    if amount < 100:
        return False, "❌ Minimum withdraw is ₹100."
    user_data = users.find_one({"_id": uid})
    if not user_data or user_data["wallet"] < amount:
        return False, "❌ Insufficient balance."
    return True, None

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def gen_link():
    """Generate random short link (no 2hr:30min logic)"""
    domains = [
        "tinyurl.com", "bit.ly", "t.ly", "short.io", "rb.gy",
        "shrtco.de", "linktr.ee", "cut.ly", "is.gd", "cli.gs"
    ]
    random_code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"https://{random.choice(domains)}/{random_code}"

def shorten(url):
    try:
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={urllib.parse.quote_plus(url)}"
        return urllib.request.urlopen(api).read().decode().strip()
    except:
        return url

async def auto_delete(msg, sec=5):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ───────── MESSAGE DELETER (GROUP ONLY) ───────── #
@Bot.on_message(filters.group & ~filters.me & ~filters.bot & ~filters.command(["/genlink"]))
async def delete_user_messages(bot, message):
    """Delete all non-bot, non-admin, non-command messages in group"""
    try:
        user_id = message.from_user.id
        # Check if user is admin
        chat_member = await bot.get_chat_member(message.chat.id, user_id)
        if chat_member.status in ['creator', 'administrator']:
            return  # Don't delete admin messages
        
        # Delete user message (not bot cmd or admin msg)
        await message.delete()
    except:
        pass

# ───────── GENLINK COMMAND (GROUP ONLY) ───────── #
@Bot.on_message(filters.command("genlink") & filters.group)
async def genlink_cmd(bot, m):
    """Generate link command - works ONLY in groups"""
    ensure_user(m.from_user.id)
    
    # Check cooldown (simple 1 hour)
    user_data = users.find_one({"_id": m.from_user.id})
    now = datetime.now()
    
    if user_data.get("last_gen") and (now - user_data["last_gen"]).seconds < 3600:
        remaining = 3600 - (now - user_data["last_gen"]).seconds
        mins = remaining // 60
        secs = remaining % 60
        return await m.reply(f"⏳ Wait {mins}m {secs}s before next /genlink", delete_after=5)
    
    # Update last gen time
    users.update_one({"_id": m.from_user.id}, {
        "$set": {"last_gen": now},
        "$inc": {"wallet": 10}  # Reward 10 coins
    })
    
    # Generate link
    link = gen_link()
    short_link = shorten(link)
    
    msg_text = f"""
🔗 **New Link Generated!**
💰 **+10 Coins Added**
🌐 **Link:** `{short_link}`
⚡ **Next:** 1 hour cooldown
    """
    
    await m.reply(msg_text, delete_after=300)  # Auto delete after 5 min

# ───────── START ───────── #
@Bot.on_message(filters.command("start"))
async def start(bot, m):
    ensure_user(m.from_user.id)
    if m.chat.type == "private":
        await m.reply("👋 Welcome! Join group to use /genlink\nhttps://t.me/+-K09FAQa85I5MDc1")
    else:
        await m.reply("✅ Bot active! Use /genlink to earn coins", delete_after=10)

# ───────── WALLET ───────── #
@Bot.on_message(filters.command("wallet"))
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    bal = users.find_one({"_id": m.from_user.id})["wallet"]
    status = "🟢 ENABLED" if withdraw_enabled() else "🔴 DISABLED"
    await m.reply(
        f"💰 **Balance:** ₹{bal}\n"
        f"📊 **Withdraw Status:** {status}\n"
        f"💳 **Minimum Withdraw:** ₹100",
        delete_after=30
    )

# ───────── ADMIN COMMANDS ───────── #
@Bot.on_message(filters.command("onwithdraw") & filters.private)
async def onwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(True)
    await m.reply("✅ Withdraw System **ENABLED**")

@Bot.on_message(filters.command("offwithdraw") & filters.private)
async def offwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(False)
    await m.reply("❌ Withdraw System **DISABLED**")

# ───────── WITHDRAW ───────── #
@Bot.on_message(filters.command("withdraw"))
async def withdraw(bot, m):
    ensure_user(m.from_user.id)
    await m.reply(
        "💸 **Withdraw Options:**\n\n"
        "📱 **UPI:** `/upiid name@upi 100`\n"
        "📧 **Gmail:** `/gmail email 100`",
        delete_after=60
    )

@Bot.on_message(filters.command("upiid"))
async def upiid(bot, m):
    try:
        upi, amt = m.command[1], int(m.command[2])
    except:
        return await m.reply("❌ **Usage:** `/upiid name@upi 100`", delete_after=10)
    
    ok, reason = can_withdraw(m.from_user.id, amt)
    if not ok:
        return await m.reply(reason, delete_after=10)
    
    wid = gen_token()
    withdraws.insert_one({
        "_id": wid, 
        "user": m.from_user.id, 
        "amount": amt, 
        "upi": upi,
        "status": "pending",
        "date": datetime.now()
    })
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{wid}"),
         InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{wid}")]
    ])
    
    await bot.send_message(
        ADMIN_CHANNEL,
        f"💸 **Withdraw Request**\n"
        f"👤 **User:** `{m.from_user.id}`\n"
        f"💰 **Amount:** ₹{amt}\n"
        f"📱 **UPI:** `{upi}`",
        reply_markup=buttons
    )
    await m.reply("✅ **Request sent to admin**", delete_after=10)

# ───────── CALLBACK HANDLERS ───────── #
@Bot.on_callback_query(filters.regex("^approve_"))
async def approve(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})
    if not data or data["status"] != "pending":
        return await q.answer("❌ Invalid/processed request")
    
    users.update_one({"_id": data["user"]}, {"$inc": {"wallet": -data["amount"]}})
    withdraws.update_one({"_id": wid}, {"$set": {"status": "approved"}})
    await bot.send_message(data["user"], "✅ **Withdraw APPROVED** ✓")
    await q.message.edit_text(q.message.text + "\n\n✅ **APPROVED** ✓")

@Bot.on_callback_query(filters.regex("^reject_"))
async def reject(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})
    if data:
        withdraws.update_one({"_id": wid}, {"$set": {"status": "rejected"}})
        await bot.send_message(data["user"], "❌ **Withdraw REJECTED**")
    await q.message.edit_text(q.message.text + "\n\n❌ **REJECTED** ✗")

# ───────── HEALTH CHECK ───────── #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Alive")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("🚀 Bot starting... Group only mode!")
    Bot.run()
