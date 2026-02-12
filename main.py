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
TARGET_GROUP = -1001892345678  # Your group ID: https://t.me/+-K09FAQa85I5MDc1

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

# ───────── MESSAGE DELETER (ONLY TARGET GROUP) ───────── #
@Bot.on_message(filters.group & filters.chat(TARGET_GROUP) & ~filters.me & ~filters.bot & ~filters.command(["/genlink"]))
async def delete_user_messages(bot, message):
    """Delete all non-bot, non-admin, non-/genlink messages ONLY in target group"""
    try:
        user_id = message.from_user.id
        # Check if user is admin
        chat_member = await bot.get_chat_member(message.chat.id, user_id)
        if chat_member.status in ['creator', 'administrator']:
            return  # Don't delete admin messages
        
        # Delete user message
        await message.delete()
    except:
        pass

# ───────── GENLINK COMMAND (TARGET GROUP ONLY) ───────── #
@Bot.on_message(filters.command("genlink") & filters.chat(TARGET_GROUP))
async def genlink_cmd(bot, m):
    """Generate link command - ONLY in target group"""
    ensure_user(m.from_user.id)
    
    # Check cooldown (1 hour)
    user_data = users.find_one({"_id": m.from_user.id})
    now = datetime.now()
    
    if user_data.get("last_gen") and (now - user_data["last_gen"]).seconds < 3600:
        remaining = 3600 - (now - user_data["last_gen"]).seconds
        mins = remaining // 60
        secs = remaining % 60
        return await m.reply(f"⏳ Wait {mins}m {secs}s before next /genlink")
    
    # Update last gen time + reward
    users.update_one({"_id": m.from_user.id}, {
        "$set": {"last_gen": now},
        "$inc": {"wallet": 10}  # +10 coins
    })
    
    # Generate link
    link = gen_link()
    short_link = shorten(link)
    
    msg_text = f"""
🔗 **New Link Generated!**
💰 **+10 Coins Added**
🌐 **Link:** `{short_link}`
⚡ **Next:** 1 hour cooldown
💳 **Balance:** `/{wallet}` (check anytime)
    """
    
    await m.reply(msg_text)

# ───────── START (WORKS EVERYWHERE) ───────── #
@Bot.on_message(filters.command("start"))
async def start(bot, m):
    ensure_user(m.from_user.id)
    if m.chat.id == TARGET_GROUP:
        await m.reply("✅ Bot active!\n🔗 Use `/genlink` to earn coins\n💰 Check `/wallet`")
    else:
        await m.reply(
            "👋 **Welcome!**\n\n"
            "🎯 **Earn Coins:** Join group & use `/genlink`\n"
            "📱 **Group:** https://t.me/+-K09FAQa85I5MDc1\n\n"
            "💰 **Commands:** `/wallet` `/withdraw`"
        )

# ───────── WALLET (WORKS EVERYWHERE) ───────── #
@Bot.on_message(filters.command("wallet"))
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    bal = users.find_one({"_id": m.from_user.id})["wallet"]
    status = "🟢 ENABLED" if withdraw_enabled() else "🔴 DISABLED"
    await m.reply(
        f"💰 **Your Balance:** ₹{bal}\n"
        f"📊 **Withdraw:** {status}\n"
        f"💳 **Min Withdraw:** ₹100\n"
        f"🔗 **Earn more:** https://t.me/+-K09FAQa85I5MDc1"
    )

# ───────── ADMIN COMMANDS (PRIVATE ONLY) ───────── #
@Bot.on_message(filters.command("onwithdraw") & filters.private)
async def onwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(True)
    await m.reply("✅ **Withdraw System ENABLED**")

@Bot.on_message(filters.command("offwithdraw") & filters.private)
async def offwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(False)
    await m.reply("❌ **Withdraw System DISABLED**")

# ───────── WITHDRAW (WORKS EVERYWHERE) ───────── #
@Bot.on_message(filters.command("withdraw"))
async def withdraw(bot, m):
    ensure_user(m.from_user.id)
    await m.reply(
        "💸 **Withdraw Options:**\n\n"
        "📱 **UPI:** `/upiid name@upi 100`\n"
        "📧 **Gmail:** `/gmail email 100`\n\n"
        "💰 Check balance: `/wallet`"
    )

@Bot.on_message(filters.command("upiid"))
async def upiid(bot, m):
    try:
        upi, amt = m.command[1], int(m.command[2])
    except:
        return await m.reply("❌ **Usage:** `/upiid name@upi 100`")
    
    ok, reason = can_withdraw(m.from_user.id, amt)
    if not ok:
        return await m.reply(reason)
    
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
        f"📱 **UPI:** `{upi}`\n"
        f"⏰ **Time:** `{datetime.now().strftime('%H:%M %d/%m')}`",
        reply_markup=buttons
    )
    await m.reply("✅ **Request sent to admin!**")

# ───────── CALLBACK HANDLERS ───────── #
@Bot.on_callback_query(filters.regex("^approve_"))
async def approve(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})
    if not data or data["status"] != "pending":
        return await q.answer("❌ Invalid request")
    
    users.update_one({"_id": data["user"]}, {"$inc": {"wallet": -data["amount"]}})
    withdraws.update_one({"_id": wid}, {"$set": {"status": "approved"}})
    await bot.send_message(data["user"], "✅ **Withdraw APPROVED!** ✓\n💰 Money sent to UPI")
    await q.message.edit_text(q.message.text + "\n\n✅ **APPROVED** ✓")

@Bot.on_callback_query(filters.regex("^reject_"))
async def reject(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})
    if data:
        withdraws.update_one({"_id": wid}, {"$set": {"status": "rejected"}})
        await bot.send_message(data["user"], "❌ **Withdraw REJECTED**\n💰 Balance restored")
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
    print("🚀 Bot starting... /genlink = GROUP ONLY, others = EVERYWHERE!")
    Bot.run()
