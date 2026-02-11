import os
import threading
import random
import string
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz

load_dotenv()

# ───────────────── MongoDB ───────────────── #
MONGO_URL = os.getenv("MONGO_URL")
client = MongoClient(MONGO_URL)
db = client["telegram_bot"]

users_collection = db["users"]
keys_collection = db["keys"]
settings_collection = db["settings"]

ADMINS = [int(i) for i in os.getenv("ADMINS", "2117119246").split()]

if not settings_collection.find_one({"_id": "genkey"}):
    settings_collection.insert_one({"_id": "genkey", "enabled": True})

# ───────────────── Bot ───────────────── #
Bot = Client(
    "Play-Store-Bot",
    bot_token=os.environ["BOT_TOKEN"],
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"]
)

JOIN_BUTTONS = [
    ("Join Channel 1", "https://t.me/+wMO973O29JEyNzRl"),
    ("Join Channel 2", "https://t.me/freefirepanellinks"),
]

# ───────────────── Time Setup ───────────────── #
IST = pytz.timezone("Asia/Kolkata")

def is_restricted_time():
    now = datetime.now(IST).time()

    # 10:01 AM – 12:59 PM
    if (now.hour == 10 and now.minute >= 1) or (11 <= now.hour <= 12):
        return True

    # 2:01 PM – 5:59 PM
    if (now.hour == 14 and now.minute >= 1) or (15 <= now.hour <= 17):
        return True

    # 7:00 PM – 8:59 AM
    if now.hour >= 19 or now.hour < 9:
        return True

    return False

# ───────────────── Helpers ───────────────── #
def ensure_user(user_id: int):
    if not users_collection.find_one({"_id": user_id}):
        users_collection.insert_one({"_id": user_id})

def gen_key(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# ───────────────── /start ───────────────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, message):
    user_id = message.from_user.id
    ensure_user(user_id)

    buttons = [
        [InlineKeyboardButton(text, url=url)]
        for text, url in JOIN_BUTTONS
    ]
    buttons.append([InlineKeyboardButton("Verify ✅", callback_data="verified")])

    await message.reply(
        "👇 **Join the channels below**\nThen click **Verify**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Bot.on_callback_query(filters.regex("^verified$"))
async def verified(bot, query):
    await query.message.edit_text(
        "✅ **Bot is Alive**\n\n"
        "**Commands:**\n"
        "/start\n"
        "/setinfo <user_id>\n"
        "/genkey\n"
        "/key <KEY>"
    )

# ───────────────── Group Message Filter ───────────────── #
@Bot.on_message(filters.group & ~filters.service)
async def group_filter(bot, message):

    text = message.text or ""

    # Allowed patterns (always allowed)
    allow_number = re.search(r"\d{5,}", text)
    allow_tvk = re.search(r"(http|https).*tvkurl\.site", text)

    # If restricted time
    if is_restricted_time():
        if allow_number or allow_tvk:
            return
        else:
            try:
                await message.delete()
            except:
                pass
            return

    # Outside restricted time → normal rule
    if allow_number or allow_tvk:
        return

    try:
        await message.delete()
    except:
        pass

# ───────────────── Delete Join & Service Messages ───────────────── #
@Bot.on_message(filters.group & filters.service)
async def delete_service(bot, message):
    try:
        await message.delete()
    except:
        pass

# ───────────────── Health Check ───────────────── #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Alive")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

# ───────────────── Main ───────────────── #
if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    Bot.run()
