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

ADMIN_CHANNEL = int(os.getenv("ADMIN_CHANNEL"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

TVKURL_API = "9986767adc94f9d0a46a66fe436a9ba577c74f1f"

# Default settings
settings.update_one({"_id": "withdraw"}, {"$setOnInsert": {"enabled": False}}, upsert=True)
settings.update_one({"_id": "earning"}, {"$setOnInsert": {"enabled": True}}, upsert=True)

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
    return s.get("enabled", False)

def earning_enabled():
    s = settings.find_one({"_id": "earning"})
    return s.get("enabled", True)

def set_withdraw(value: bool):
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": value}}, upsert=True)

def set_earning(value: bool):
    settings.update_one({"_id": "earning"}, {"$set": {"enabled": value}}, upsert=True)

def can_withdraw(uid, amount):
    if not withdraw_enabled():
        return False, "❌ Withdraw system is OFF."
    if amount < 100:
        return False, "❌ Minimum withdraw is ₹100."
    user = users.find_one({"_id": uid})
    if not user or user.get("wallet", 0) < amount:
        return False, "❌ Insufficient balance."
    return True, None

# ───────── WITHDRAW CONTROL COMMANDS ───────── #

@Bot.on_message(filters.command("onwithdraw") & filters.private)
async def onwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(True)
    await m.reply("✅ Withdraw system turned ON.")

@Bot.on_message(filters.command("offwithdraw") & filters.private)
async def offwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(False)
    await m.reply("❌ Withdraw system turned OFF.")

# ───────── ONTIME / OFFTIME ───────── #

@Bot.on_message(filters.command("ontime") & filters.private)
async def ontime(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_earning(True)
    await m.reply("✅ Earning system turned ON.")

@Bot.on_message(filters.command("offtime") & filters.private)
async def offtime(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_earning(False)
    await m.reply("❌ Earning system turned OFF.")

# ───────── GENLINK ───────── #

@Bot.on_message(filters.command("genlink"))
async def genlink(bot, m):

    if not earning_enabled():
        return await m.reply("⛔ Earning system is currently OFF. Try later.")

    ensure_user(m.from_user.id)
    user_data = users.find_one({"_id": m.from_user.id})
    now = datetime.utcnow()

    last_gen = user_data.get("last_gen")
    if last_gen and now - last_gen < timedelta(hours=1):
        remaining = timedelta(hours=1) - (now - last_gen)
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
        "💰 Your ₹1.5 Reward Link\n"
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

        if not earning_enabled():
            return await m.reply("⛔ Earning system is OFF.")

        token = m.command[1].replace("reward_", "")
        data = rewards.find_one({"token": token})

        if not data:
            return await m.reply("❌ Invalid token.")
        if data["used"]:
            return await m.reply("❌ Token already used.")
        if data["user"] != m.from_user.id:
            return await m.reply("❌ This link is not yours.")
        if datetime.utcnow() - data["created_at"] > timedelta(hours=1):
            return await m.reply("❌ Token expired.")

        rewards.update_one({"token": token}, {"$set": {"used": True}})
        users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 1.5}})

        return await m.reply("✅ ₹1.5 added to your wallet!")

    await m.reply(
        "👋 Welcome!\n\n"
        "/genlink - Generate earning link\n"
        "/wallet - Check balance\n"
        "/withdraw - Cash out earnings\n"
        "/redeemgift CODE - Redeem gift code"
    )

# ───────── HEALTH CHECK ───────── #

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Alive")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("🚀 Bot Running (Withdraw Control Added)")
    Bot.run()
