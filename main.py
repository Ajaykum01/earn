import os
import threading
import random
import string
import urllib.parse
import urllib.request
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

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
TARGET_GROUP = -1001892345678  # https://t.me/+-K09FAQa85I5MDc1

# Ensure withdraw setting exists
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
        users.insert_one({"_id": uid, "wallet": 0})

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
    if not user_data or user_data.get("wallet", 0) < amount:
        return False, "❌ Insufficient balance."
    return True, None

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def gen_link():
    """Simple random link generator"""
    domains = ["tinyurl.com", "bit.ly", "t.ly", "rb.gy", "shrtco.de", "cut.ly", "is.gd"]
    code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=7))
    return f"https://{random.choice(domains)}/{code}"

def shorten(url):
    try:
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={urllib.parse.quote_plus(url)}"
        return urllib.request.urlopen(api).read().decode().strip()
    except:
        return url

# ───────── /GENLINK - GROUP ONLY (NO COOLDOWN) ───────── #
@Bot.on_message(filters.command("genlink") & filters.chat(TARGET_GROUP))
async def genlink(bot, m):
    """/genlink works ONLY in target group - NO COOLDOWN"""
    ensure_user(m.from_user.id)
    
    # Give +10 coins every time (NO cooldown)
    users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 10}})
    
    # Generate link
    link = gen_link()
    short_link = shorten(link)
    
    await m.reply(
        f"🔗 **Link Generated!**\n"
        f"💰 **+10 Coins Added**\n"
        f"🌐 `{short_link}`\n"
        f"✅ **Ready for next!**"
    )

# ───────── /START - PRIVATE ONLY ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)
    await m.reply(
        "👋 **Welcome!**\n\n"
        "🎯 **Earn Coins:** `/genlink` in group\n"
        "📱 **Group:** https://t.me/+-K09FAQa85I5MDc1\n\n"
        "💰 **Check:** `/wallet`\n"
        "💸 **Withdraw:** `/withdraw`"
    )

# ───────── ADMIN COMMANDS - PRIVATE ONLY ───────── #
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

# ───────── /WALLET - PRIVATE ONLY (FIXED KeyError) ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    user_data = users.find_one({"_id": m.from_user.id})
    bal = user_data.get("wallet", 0) if user_data else 0  # FIXED: Safe wallet access
    status = "🟢 ENABLED" if withdraw_enabled() else "🔴 DISABLED"
    await m.reply(
        f"💰 **Balance:** ₹{bal}\n\n"
        f"📊 **Withdraw Status:** {status}\n"
        f"💳 **Minimum:** ₹100\n"
        f"🔗 **Earn:** https://t.me/+-K09FAQa85I5MDc1"
    )

# ───────── /WITHDRAW - PRIVATE ONLY (FIXED delete_after) ───────── #
@Bot.on_message(filters.command("withdraw") & filters.private)
async def withdraw(bot, m):
    msg = await m.reply(
        "💸 **Withdraw Options:**\n\n"
        "📱 **UPI:** `/upiid name@upi 100`\n"
        "💰 **Balance:** `/wallet`"
    )

# ───────── /UPIID - PRIVATE ONLY ───────── #
@Bot.on_message(filters.command("upiid") & filters.private)
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
        [
            InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{wid}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{wid}")
        ]
    ])
    
    await bot.send_message(
        ADMIN_CHANNEL,
        f"💸 **Withdraw Request**\n"
        f"👤 User: `{m.from_user.id}`\n"
        f"💰 Amount: ₹{amt}\n"
        f"📱 UPI: `{upi}`",
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
    await bot.send_message(data["user"], "✅ **Withdraw APPROVED** ✓")
    await q.message.edit_text(q.message.text + "\n✅ **APPROVED**")

@Bot.on_callback_query(filters.regex("^reject_"))
async def reject(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})
    if data:
        withdraws.update_one({"_id": wid}, {"$set": {"status": "rejected"}})
    await q.message.edit_text(q.message.text + "\n❌ **REJECTED**")

# ───────── HEALTH CHECK ───────── #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Alive")
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("🚀 Bot Started - ALL ERRORS FIXED!")
    Bot.run()
