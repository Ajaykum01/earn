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

# 🔴 PUT YOUR GROUP ID HERE
TARGET_GROUP_ID = -1003865283730  

ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

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

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def shorten(url):
    try:
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={urllib.parse.quote_plus(url)}"
        result = urllib.request.urlopen(api, timeout=10).read().decode().strip()
        if result.startswith("http"):
            return result
        return url
    except:
        return url

async def auto_delete(msg, sec):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ───────── GROUP MESSAGE CONTROL ───────── #
@Bot.on_message(filters.group)
async def group_control(bot, m):

    # Only allow specific group
    if m.chat.id != TARGET_GROUP_ID:
        return

    # Allow bot messages
    if m.from_user and m.from_user.is_bot:
        return

    # Allow admins
    if m.from_user and m.from_user.id in ADMINS:
        return

    # Allow only /genlink
    if m.text and m.text.startswith("/genlink"):
        return

    # Delete everything else
    try:
        await m.delete()
    except:
        pass


# ───────── GENLINK (GROUP ONLY) ───────── #
@Bot.on_message(filters.command("genlink") & filters.group)
async def genlink(bot, m):

    if m.chat.id != TARGET_GROUP_ID:
        return

    uid = m.from_user.id
    ensure_user(uid)

    token = gen_token()

    rewards.insert_one({
        "token": token,
        "user": uid,
        "used": False,
        "created_at": datetime.utcnow()
    })

    me = await bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=reward_{token}"
    short_link = shorten(deep_link)

    msg = await m.reply(
        "💰 Here is your ₹5 Reward Link\n"
        "🔐 Complete the link to receive reward.",
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

        if data["used"]:
            return await m.reply("❌ Already used.")

        if data["user"] != m.from_user.id:
            return await m.reply("❌ This link is not yours.")

        rewards.update_one({"token": token}, {"$set": {"used": True}})
        users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 5}})

        return await m.reply("✅ ₹5 added to your wallet!")

    await m.reply("👋 Welcome!")

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
