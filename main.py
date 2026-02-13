import asyncio
import os
import random
import string
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient

load_dotenv()

# ===== Configuration =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
MONGO_URL = os.getenv("MONGO_URL", "")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split() if x.strip()]
WITHDRAW_CHANNEL = -1003624079737
TVK_API_TOKEN = "9986767adc94f9d0a46a66fe436a9ba577c74f1f"

# ===== Database =====
mongo = MongoClient(MONGO_URL)
db = mongo["earn_bot"]
users = db["users"]
reward_tokens = db["reward_tokens"]
gift_codes = db["gift_codes"]
withdraw_requests = db["withdraw_requests"]
settings = db["settings"]

# Initialize Settings
settings.update_one({"_id": "withdraw"}, {"$setOnInsert": {"enabled": True}}, upsert=True)
settings.update_one({"_id": "time_gap"}, {"$setOnInsert": {"enabled": True}}, upsert=True)

def ensure_user(user_id: int):
    if not users.find_one({"_id": user_id}):
        users.insert_one({"_id": user_id, "wallet": 0.0, "last_gen": None})

def is_admin(user_id: int):
    return user_id in ADMINS

def withdraw_enabled():
    row = settings.find_one({"_id": "withdraw"})
    return bool(row and row.get("enabled", False))

def time_gap_enabled():
    row = settings.find_one({"_id": "time_gap"})
    return bool(row and row.get("enabled", False))

def new_code(length=8):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

def shorten_tvk(url: str):
    alias = new_code(6)
    api = (
        "https://tvkurl.site/api?"
        f"api={TVK_API_TOKEN}&"
        f"url={urllib.parse.quote_plus(url)}&"
        f"alias={alias}&format=text"
    )
    try:
        result = urllib.request.urlopen(api, timeout=20).read().decode().strip()
        return result if result else url
    except:
        return url

def fmt_money(amount: float):
    return ("{:.2f}".format(amount)).rstrip("0").rstrip(".")


bot = Client("earn-bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# =========================
# ====== GENLINK =========
# =========================
@bot.on_message(filters.command("genlink") & (filters.group | filters.private))
async def cmd_genlink(client, message):
    ensure_user(message.from_user.id)

    user = users.find_one({"_id": message.from_user.id})
    last_gen = user.get("last_gen")

    if time_gap_enabled() and last_gen and datetime.utcnow() - last_gen < timedelta(hours=1):
        wait_left = timedelta(hours=1) - (datetime.utcnow() - last_gen)
        mins = int(wait_left.total_seconds() // 60)
        return await message.reply(f"⏳ Wait {mins} minutes before generating a new link.")

    token = new_code(10)
    reward_tokens.insert_one({
        "token": token,
        "owner_id": message.from_user.id,
        "used": False,
        "created_at": datetime.utcnow(),
    })

    users.update_one({"_id": message.from_user.id}, {"$set": {"last_gen": datetime.utcnow()}})

    me = await client.get_me()
    deep_link = f"https://t.me/{me.username}?start=reward_{token}"
    short_link = shorten_tvk(deep_link)

    await message.reply(
        "🔗 Your earning link is ready!\n"
        "• Reward: ₹1.5 (one-time use)\n"
        "• Only you can claim this link\n\n"
        f"{short_link}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open Link", url=short_link)]]
        ),
        disable_web_page_preview=True,
    )


# =====================================================
# =============== ON & OFF TIME CONTROL ===============
# =====================================================

@bot.on_message(filters.command("ontime") & filters.private)
async def cmd_ontime(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "time_gap"}, {"$set": {"enabled": True}}, upsert=True)
    await message.reply("✅ Genlink 1-hour cooldown is now ON.")

@bot.on_message(filters.command("offtime") & filters.private)
async def cmd_offtime(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "time_gap"}, {"$set": {"enabled": False}}, upsert=True)
    await message.reply("✅ Genlink 1-hour cooldown is now OFF.")


# =========================
# ===== Health Server =====
# =========================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    HTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.run()
