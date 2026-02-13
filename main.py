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

ADMIN_CHANNEL = -1003624079737 # Hardcoded as per your request
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

TVKURL_API = "9986767adc94f9d0a46a66fe436a9ba577c74f1f"

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
        users.insert_one({
            "_id": uid,
            "wallet": 0,
            "last_gen": None
        })

def gen_token(n=12):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def shorten_with_tvkurl(long_url):
    try:
        api_url = f"https://tvkurl.site/api?api={TVKURL_API}&url={urllib.parse.quote_plus(long_url)}&format=text"
        result = urllib.request.urlopen(api_url, timeout=10).read().decode().strip()
        if result.startswith("http"):
            return result
        return long_url
    except:
        return long_url

def withdraw_enabled():
    s = settings.find_one({"_id": "withdraw"})
    return s.get("enabled", True)

def genlink_time_enabled():
    s = settings.find_one({"_id": "genlink_time"})
    return s.get("enabled", True)

def get_genlink_cooldown_hours():
    s = settings.find_one({"_id": "genlink_time"})
    return s.get("hours", 1)

def set_genlink_time(enabled: bool, hours: int = 1):
    settings.update_one({"_id": "genlink_time"}, {"$set": {"enabled": enabled, "hours": hours}}, upsert=True)

# ───────── GENLINK ───────── #
@Bot.on_message(filters.command("genlink") & filters.private)
async def genlink(bot, m):
    ensure_user(m.from_user.id)
    user_data = users.find_one({"_id": m.from_user.id})
    now = datetime.utcnow()

    cooldown_hours = get_genlink_cooldown_hours() if genlink_time_enabled() else 0
    last_gen = user_data.get("last_gen")
    
    if cooldown_hours > 0 and last_gen and now - last_gen < timedelta(hours=cooldown_hours):
        remaining = timedelta(hours=cooldown_hours) - (now - last_gen)
        minutes = int(remaining.total_seconds() // 60)
        return await m.reply(f"⏳ Wait {minutes} minutes before generating next link.")

    token = gen_token()
    rewards.insert_one({
        "token": token,
        "user": m.from_user.id,
        "used": False,
        "created_at": now
    })

    users.update_one({"_id": m.from_user.id}, {"$set": {"last_gen": now}})
    me = await bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=reward_{token}"
    tvk_short = shorten_with_tvkurl(deep_link)

    await m.reply(
        "💰 **Your ₹1.5 Reward Link**\n"
        "⏳ Valid 60 Minutes\n\n"
        "Complete the shortlink to earn.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open Short Link", url=tvk_short)]
        ])
    )

# ───────── START ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)

    if len(m.command) > 1 and m.command[1].startswith("reward_"):
        token = m.command[1].replace("reward_", "")
        data = rewards.find_one({"token": token})

        if not data: return await m.reply("❌ Invalid token.")
        if data["used"]: return await m.reply("❌ Token already used.")
        if data["user"] != m.from_user.id: return await m.reply("❌ This link is not yours.")
        if datetime.utcnow() - data["created_at"] > timedelta(hours=1):
            return await m.reply("❌ Token expired.")

        rewards.update_one({"token": token}, {"$set": {"used": True}})
        users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 1.5}})
        return await m.reply("✅ ₹1.5 added to your wallet!")

    cd_status = "ON" if genlink_time_enabled() else "OFF"
    hrs = get_genlink_cooldown_hours()
    
    await m.reply(
        "👋 **Welcome!**\n\n"
        "• /genlink - Generate earning link\n"
        "• /wallet - Check balance\n"
        "• /withdraw - Cash out earnings\n"
        "• /redeemgift - Redeem gift code\n\n"
        f"**Genlink cooldown:** {cd_status} ({hrs}h)"
    )

# ───────── WALLET ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    user = users.find_one({"_id": m.from_user.id})
    bal = user.get("wallet", 0)
    await m.reply(f"💰 **Your Balance:** ₹{bal}")

# ───────── WITHDRAW SYSTEM ───────── #

@Bot.on_message(filters.command("withdraw") & filters.private)
async def withdraw_cmd(bot, m):
    if not withdraw_enabled():
        return await m.reply("❌ Withdrawal system is currently disabled.")
    
    ensure_user(m.from_user.id)
    user = users.find_one({"_id": m.from_user.id})
    bal = user.get("wallet", 0)

    if bal < 100:
        return await m.reply(f"❌ Minimum withdrawal is **₹100**.\n💰 Your balance: ₹{bal}")

    await m.reply(
        "🏦 **Choose Withdrawal Method:**\n\n"
        "Use `/upiid your@upi` to withdraw via UPI\n"
        "Use `/gmail your@email.com` to withdraw via Gift Card",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Withdraw UPI", switch_inline_query_current_chat="/upiid ")],
            [InlineKeyboardButton("Withdraw Gmail", switch_inline_query_current_chat="/gmail ")]
        ])
    )

@Bot.on_message(filters.command(["upiid", "gmail"]) & filters.private)
async def process_withdraw(bot, m):
    if not withdraw_enabled():
        return await m.reply("❌ Withdrawal system is OFF.")
        
    ensure_user(m.from_user.id)
    if len(m.command) < 2:
        return await m.reply(f"Please provide details. Usage: `/{m.command[0]} address`")
    
    address = m.text.split(None, 1)[1]
    user_id = m.from_user.id
    user = users.find_one({"_id": user_id})
    balance = user.get("wallet", 0)

    if balance < 100:
        return await m.reply(f"❌ Minimum ₹100 required. Balance: ₹{balance}")

    withdraw_id = gen_token(8)
    withdraws.insert_one({
        "_id": withdraw_id,
        "user_id": user_id,
        "amount": balance,
        "address": address,
        "type": m.command[0],
        "status": "pending"
    })

    # Reset balance
    users.update_one({"_id": user_id}, {"$set": {"wallet": 0}})

    admin_msg = (
        f"💰 **New Withdrawal Request**\n\n"
        f"👤 **User:** `{user_id}`\n"
        f"💵 **Amount:** ₹{balance}\n"
        f"🏷 **Method:** {m.command[0].upper()}\n"
        f"📍 **Address:** `{address}`\n"
        f"🆔 **ID:** `{withdraw_id}`"
    )
    
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{withdraw_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject_{withdraw_id}")
        ]
    ])

    await bot.send_message(ADMIN_CHANNEL, admin_msg, reply_markup=kb)
    await m.reply("✅ Withdrawal request sent to admin! Balance has been locked for processing.")

# ───────── ADMIN CALLBACK ───────── #

@Bot.on_callback_query(filters.regex(r"^wd_(approve|reject)_"))
async def handle_withdraw_callback(bot, cb):
    data = cb.data.split("_")
    action = data[1]
    wd_id = data[2]

    wd_request = withdraws.find_one({"_id": wd_id})
    if not wd_request or wd_request["status"] != "pending":
        return await cb.answer("Request already processed!", show_alert=True)

    user_id = wd_request["user_id"]
    amount = wd_request["amount"]

    if action == "approve":
        withdraws.update_one({"_id": wd_id}, {"$set": {"status": "approved"}})
        try:
            await bot.send_message(user_id, f"✅ **Withdrawal Approved!**\n₹{amount} has been sent to your account.")
        except: pass
        status_text = "✅ APPROVED"
    else:
        users.update_one({"_id": user_id}, {"$inc": {"wallet": amount}})
        withdraws.update_one({"_id": wd_id}, {"$set": {"status": "rejected"}})
        try:
            await bot.send_message(user_id, f"❌ **Withdrawal Rejected.**\n₹{amount} has been refunded to your wallet.")
        except: pass
        status_text = "❌ REJECTED & REFUNDED"

    await cb.message.edit_text(f"{cb.message.text}\n\n**Status:** {status_text}")
    await cb.answer(f"Success: {action}")

# ───────── HEALTH CHECK & RUN ───────── #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Alive")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("🚀 Bot Running Successfully")
    Bot.run()
