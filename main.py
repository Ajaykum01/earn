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
from datetime import datetime, timedelta, UTC

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
        users.insert_one({
            "_id": uid,
            "wallet": 0,
            "last_gen": None
        })

def withdraw_enabled():
    s = settings.find_one({"_id": "withdraw"})
    return s.get("enabled", False)

def set_withdraw(value: bool):
    settings.update_one(
        {"_id": "withdraw"},
        {"$set": {"enabled": value}},
        upsert=True
    )

def can_withdraw(uid, amount):
    if not withdraw_enabled():
        return False, "❌ Withdraw is OFF by admin."
    if amount < 100:
        return False, "❌ Minimum withdraw is ₹100."
    user = users.find_one({"_id": uid})
    if not user or user.get("wallet", 0) < amount:
        return False, "❌ Insufficient balance."
    return True, None

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def shorten(url):
    try:
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={urllib.parse.quote_plus(url)}"
        result = urllib.request.urlopen(api, timeout=10).read().decode().strip()

        # validate URL
        if result.startswith("http://") or result.startswith("https://"):
            return result
        else:
            return url
    except:
        return url

async def auto_delete(msg, sec):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ───────── GROUP LOCK (DELETE USER MESSAGES ONLY) ───────── #
@Bot.on_message(filters.group)
async def delete_group_messages(bot, m):

    # Skip if message from bot itself
    if m.from_user and m.from_user.is_bot:
        return

    # Skip admins
    if m.from_user and m.from_user.id in ADMINS:
        return

    # Allow /genlink only
    if m.text and m.text.startswith("/genlink"):
        return

    # Delete everything else
    try:
        await m.delete()
    except:
        pass

# ───────── GENLINK (TVKURL TOKEN SYSTEM) ───────── #
@Bot.on_message(filters.command("genlink") & filters.group)
async def genlink(bot, m):
    uid = m.from_user.id
    ensure_user(uid)

    user = users.find_one({"_id": uid}) or {}
    last_gen = user.get("last_gen")

    now = datetime.now(UTC)

    # 2h30m cooldown
    if last_gen:
        if now - last_gen < timedelta(hours=2, minutes=30):
            return await m.reply("⏳ Wait 2h30m before generating again.")

    # Generate reward token
    token = gen_token()

    rewards.insert_one({
        "token": token,
        "user": uid,
        "used": False,
        "created_at": now
    })

    users.update_one(
        {"_id": uid},
        {"$set": {"last_gen": now}},
        upsert=True
    )

    # Create deep link
    me = await bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=reward_{token}"

    # Shorten using TVKURL
    short_link = shorten(deep_link)

    # Safety check (important)
    if not short_link.startswith("http"):
        short_link = deep_link

    msg = await m.reply(
        "💰 Here is your ₹5 Reward Link\n\n"
        "🔐 Complete the short link to receive reward.\n"
        "⏱ Valid for 30 minutes only.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open Link", url=short_link)]
        ])
    )

    asyncio.create_task(auto_delete(msg, 1200))
# ───────── START + CLAIM ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)

    if len(m.command) > 1 and m.command[1].startswith("reward_"):
        token = m.command[1].split("_")[1]
        data = rewards.find_one({"token": token})

        if not data:
            return await m.reply("❌ Invalid token.")

        if data.get("used"):
            return await m.reply("❌ Already used.")

        if data.get("user") != m.from_user.id:
            return await m.reply("❌ This link is not yours.")

        if datetime.now(UTC) - data.get("created_at") > timedelta(minutes=30):
            return await m.reply("❌ Token expired.")

        rewards.update_one({"token": token}, {"$set": {"used": True}})
        users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 5}})

        return await m.reply("✅ ₹5 added to your wallet!")

    await m.reply("👋 Welcome! Use /wallet to check balance.")

# ───────── WALLET ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    user = users.find_one({"_id": m.from_user.id})
    bal = user.get("wallet", 0)
    status = "🟢 ENABLED" if withdraw_enabled() else "🔴 DISABLED"

    await m.reply(
        f"💰 Balance: ₹{bal}\n\n"
        f"Withdraw Status: {status}\n"
        f"Minimum Withdraw: ₹100"
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
    Bot.run()
