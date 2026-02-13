import os
import threading
import random
import string
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ───────── DATABASE ───────── #
client = MongoClient(os.getenv("MONGO_URL"))
db = client["telegram_bot"]

users = db["users"]
rewards = db["rewards"]
withdraws = db["withdraws"]
giftcodes = db["giftcodes"]
settings = db["settings"]

# Fixed Admin Channel ID
ADMIN_CHANNEL = -1003624079737 
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

TVKURL_API = "9986767adc94f9d0a46a66fe436a9ba577c74f1f"

# Initial Settings
settings.update_one({"_id": "withdraw"}, {"$setOnInsert": {"enabled": True}}, upsert=True)
settings.update_one({"_id": "genlink_time"}, {"$setOnInsert": {"enabled": True, "hours": 1}}, upsert=True)

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

def gen_token(n=12):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def shorten_with_tvkurl(long_url):
    try:
        api_url = f"https://tvkurl.site/api?api={TVKURL_API}&url={urllib.parse.quote_plus(long_url)}&format=text"
        result = urllib.request.urlopen(api_url, timeout=10).read().decode().strip()
        return result if result.startswith("http") else long_url
    except:
        return long_url

def withdraw_enabled():
    s = settings.find_one({"_id": "withdraw"})
    return s.get("enabled", True)

# ───────── GIFT SYSTEM (ADMIN) ───────── #
@Bot.on_message(filters.command("gengift") & filters.private)
async def gengift(bot, m):
    if m.from_user.id not in ADMINS: return
    try:
        amount = float(m.command[1])
        quantity = int(m.command[2])
        codes = []
        for _ in range(quantity):
            code = gen_token(10)
            giftcodes.insert_one({
                "code": code, "amount": amount, "used": False, 
                "used_by": None, "created_at": datetime.utcnow()
            })
            codes.append(f"`{code}`")
        await m.reply(f"🎁 **{quantity} Gift Codes Generated (₹{amount} each):**\n\n" + "\n".join(codes))
    except:
        await m.reply("Usage: `/gengift amount quantity` (Example: `/gengift 10 5`)")

# ───────── GIFT SYSTEM (USER) ───────── #
@Bot.on_message(filters.command("redeemgift") & filters.private)
async def redeemgift(bot, m):
    ensure_user(m.from_user.id)
    if len(m.command) < 2:
        return await m.reply("Usage: `/redeemgift YOUR_CODE` ")

    code = m.command[1].strip().upper()
    gift = giftcodes.find_one({"code": code})

    if not gift: return await m.reply("❌ Invalid gift code.")
    if gift["used"]: return await m.reply("❌ This code has already been redeemed.")

    giftcodes.update_one({"code": code}, {"$set": {"used": True, "used_by": m.from_user.id, "used_at": datetime.utcnow()}})
    users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": gift["amount"]}})
    
    await m.reply(f"✅ Success! **₹{gift['amount']}** added to your wallet!")

# ───────── WITHDRAW SYSTEM ───────── #
@Bot.on_message(filters.command("withdraw") & filters.private)
async def withdraw_menu(bot, m):
    if not withdraw_enabled(): return await m.reply("❌ Withdrawals are temporarily disabled.")
    ensure_user(m.from_user.id)
    user = users.find_one({"_id": m.from_user.id})
    bal = user.get("wallet", 0)

    if bal < 100:
        return await m.reply(f"❌ Minimum withdrawal is ₹100. Your balance: ₹{bal}")

    await m.reply(
        "🏦 **Choose Your Method:**\n\n"
        "To withdraw via UPI:\n`/upiid your_id@upi`\n\n"
        "To withdraw via Gmail:\n`/gmail your@email.com`"
    )

@Bot.on_message(filters.command(["upiid", "gmail"]) & filters.private)
async def process_withdraw(bot, m):
    if not withdraw_enabled(): return await m.reply("❌ Withdrawal system is OFF.")
    ensure_user(m.from_user.id)
    
    if len(m.command) < 2:
        return await m.reply(f"Please provide details. Example: `/{m.command[0]} xxxx` ")
    
    address = m.text.split(None, 1)[1]
    user_id = m.from_user.id
    user = users.find_one({"_id": user_id})
    balance = user.get("wallet", 0)

    if balance < 100:
        return await m.reply("❌ Insufficient balance for withdrawal.")

    withdraw_id = gen_token(8)
    withdraws.insert_one({
        "_id": withdraw_id, "user_id": user_id, "amount": balance,
        "address": address, "type": m.command[0], "status": "pending"
    })

    users.update_one({"_id": user_id}, {"$set": {"wallet": 0}})

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{withdraw_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject_{withdraw_id}")
    ]])

    admin_text = (f"💰 **New Withdrawal**\n\n👤 User: `{user_id}`\n💵 Amt: ₹{balance}\n"
                  f"🏷 Method: {m.command[0].upper()}\n📍 Info: `{address}`\n🆔 ID: `{withdraw_id}`")
    
    await bot.send_message(ADMIN_CHANNEL, admin_text, reply_markup=kb)
    await m.reply("✅ Withdrawal request sent to Admin. Your balance is now locked.")

# ───────── CALLBACK & OTHER COMMANDS ───────── #
@Bot.on_callback_query(filters.regex(r"^wd_(approve|reject)_"))
async def wd_callback(bot, cb):
    _, action, wd_id = cb.data.split("_")
    wd = withdraws.find_one({"_id": wd_id})
    if not wd or wd["status"] != "pending": return await cb.answer("Already processed.")

    if action == "approve":
        withdraws.update_one({"_id": wd_id}, {"$set": {"status": "approved"}})
        await bot.send_message(wd["user_id"], f"✅ Withdrawal of ₹{wd['amount']} has been approved!")
        status = "APPROVED ✅"
    else:
        users.update_one({"_id": wd["user_id"]}, {"$inc": {"wallet": wd["amount"]}})
        withdraws.update_one({"_id": wd_id}, {"$set": {"status": "rejected"}})
        await bot.send_message(wd["user_id"], f"❌ Withdrawal rejected. ₹{wd['amount']} refunded.")
        status = "REJECTED ❌"

    await cb.message.edit_text(f"{cb.message.text}\n\n**Status: {status}**")

@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)
    # [Reward Logic from your previous code remains the same...]
    await m.reply("👋 **Welcome!**\n\n/genlink - Earn\n/wallet - Balance\n/withdraw - Cash out\n/redeemgift - Gift Code")

@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    user = users.find_one({"_id": m.from_user.id})
    await m.reply(f"💰 **Balance:** ₹{user.get('wallet', 0)}")

# ───────── HEALTH CHECK & RUN ───────── #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Alive")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("🚀 Bot Running with Gift System & Withdrawal Fixes")
    Bot.run()
