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
settings = db["settings"]

ADMIN_CHANNEL = int(os.getenv("ADMIN_CHANNEL"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

# Ensure withdraw setting exists
settings.update_one(
    {"_id": "withdraw"},
    {"$setOnInsert": {"enabled": False}},
    upsert=True
)

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
    return settings.find_one({"_id": "withdraw"}).get("enabled", False)

def set_withdraw(value: bool):
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": value}}, upsert=True)

def can_withdraw(uid, amount):
    if not withdraw_enabled():
        return False, "❌ Withdraw is OFF by admin."
    if amount < 100:
        return False, "❌ Minimum withdraw is ₹100."
    if users.find_one({"_id": uid})["wallet"] < amount:
        return False, "❌ Insufficient balance."
    return True, None

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def shorten(url):
    try:
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={urllib.parse.quote_plus(url)}"
        return urllib.request.urlopen(api).read().decode().strip()
    except:
        return url

async def auto_delete(msg, sec):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ───────── GROUP LOCK (DELETE EVERYTHING EXCEPT /genlink) ───────── #
@Bot.on_message(filters.group & ~filters.command("genlink"))
async def delete_all(bot, m):
    if m.from_user and m.from_user.id in ADMINS:
        return  # allow admins to talk
    try:
        await m.delete()
    except:
        pass

# ───────── GENLINK (GROUP ONLY) ───────── #
@Bot.on_message(filters.command("genlink") & filters.group)
async def genlink(bot, m):
    uid = m.from_user.id
    ensure_user(uid)

    user = users.find_one({"_id": uid})

    if user["last_gen"] and datetime.utcnow() - user["last_gen"] < timedelta(hours=2, minutes=30):
        return await m.reply("⏳ Wait 2h30m before generating again.")

    token = gen_token()

    rewards.insert_one({
        "token": token,
        "user": uid,
        "used": False,
        "created_at": datetime.utcnow()
    })

    users.update_one({"_id": uid}, {"$set": {"last_gen": datetime.utcnow()}})

    me = await bot.get_me()
    short = shorten(f"https://t.me/{me.username}?start=reward_{token}")

    msg = await m.reply(
        "💰 Here is your ₹1.5 Key Token\n⏱ Valid for 30 minutes.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open Link", url=short)]
        ])
    )

    asyncio.create_task(auto_delete(msg, 1200))

# ───────── START / CLAIM ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)

    if len(m.command) > 1 and m.command[1].startswith("reward_"):
        token = m.command[1].split("_")[1]
        data = rewards.find_one({"token": token})

        if not data or data["used"] or data["user"] != m.from_user.id:
            return await m.reply("❌ Invalid or used token.")

        if datetime.utcnow() - data["created_at"] > timedelta(minutes=30):
            return await m.reply("❌ Token expired.")

        rewards.update_one({"token": token}, {"$set": {"used": True}})
        users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 1.5}})

        return await m.reply("✅ ₹1.5 added to your wallet!")

    await m.reply("👋 Welcome! Use /wallet to see earnings. /withdraw to send earnings to your accounts")

# ───────── WALLET ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    bal = users.find_one({"_id": m.from_user.id})["wallet"]
    status = "🟢 ENABLED" if withdraw_enabled() else "🔴 DISABLED"

    await m.reply(f"💰 Balance: ₹{bal}\nWithdraw Status: {status}\nMinimum Withdraw: ₹100\nWithdraw open every month satrting 1st and 2nd day")

# ───────── WITHDRAW SWITCH ───────── #
@Bot.on_message(filters.command("onwithdraw") & filters.private)
async def onwithdraw(bot, m):
    if m.from_user.id in ADMINS:
        set_withdraw(True)
        await m.reply("✅ Withdraw Enabled")

@Bot.on_message(filters.command("offwithdraw") & filters.private)
async def offwithdraw(bot, m):
    if m.from_user.id in ADMINS:
        set_withdraw(False)
        await m.reply("❌ Withdraw Disabled")

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
    Bot.run()
