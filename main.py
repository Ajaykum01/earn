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

load_dotenv()

# ───────────────── MongoDB ───────────────── #
MONGO_URL = os.getenv("MONGO_URL")
client = MongoClient(MONGO_URL)
db = client["telegram_bot"]

users_collection = db["users"]
keys_collection = db["keys"]
settings_collection = db["settings"]

ADMINS = [int(i) for i in os.getenv("ADMINS", "2117119246").split()]

# default genkey setting
if not settings_collection.find_one({"_id": "genkey"}):
    settings_collection.insert_one({"_id": "genkey", "enabled": True})

# ───────────────── Bot ───────────────── #
Bot = Client(
    "Play-Store-Bot",
    bot_token=os.environ["BOT_TOKEN"],
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"]
)

# Channels to SHOW (no checking)
JOIN_BUTTONS = [
    ("Join Channel 1", "https://t.me/+wMO973O29JEyNzRl"),
    ("Join Channel 2", "https://t.me/freefirepanellinks"),
]

# ───────────────── Helpers ───────────────── #
def ensure_user(user_id: int):
    if not users_collection.find_one({"_id": user_id}):
        users_collection.insert_one({"_id": user_id})

def gen_key(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def shorten_with_tvkurl(long_url: str) -> str:
    try:
        encoded = urllib.parse.quote_plus(long_url)
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={encoded}&format=text"
        with urllib.request.urlopen(api, timeout=15) as r:
            result = r.read().decode().strip()
            if result.startswith("http"):
                return result
    except:
        pass
    return long_url

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

# ───────────────── /setinfo ───────────────── #
@Bot.on_message(filters.command("setinfo") & filters.private)
async def setinfo(bot, message):
    if len(message.command) != 2 or not message.command[1].isdigit():
        return await message.reply("Usage: `/setinfo 585227752`")

    users_collection.update_one(
        {"_id": message.from_user.id},
        {"$set": {"target_id": message.command[1]}},
        upsert=True
    )
    await message.reply("✅ User ID saved")

# ───────────────── /genkey ───────────────── #
@Bot.on_message(filters.command("genkey") & filters.private)
async def genkey(bot, message):
    state = settings_collection.find_one({"_id": "genkey"})
    if not state["enabled"]:
        return await message.reply(
            "❌ **/genkey OFF**\n\n"
            "⏰ Available timings:\n"
            "9am–10am\n12pm–1pm\n6pm–7pm\n9pm–10pm"
        )

    key = gen_key()
    keys_collection.insert_one({
        "key": key,
        "owner": message.from_user.id,
        "expires": datetime.utcnow() + timedelta(days=1)
    })

    await message.reply(f"🔑 **Your Key:** `{key}`\n⏳ Valid for 24 hours")

# ───────────────── /key ───────────────── #
@Bot.on_message(filters.command("key") & filters.private)
async def use_key(bot, message):
    if len(message.command) != 2:
        return await message.reply("Usage: `/key ABC123`")

    key = message.command[1]
    data = keys_collection.find_one({"key": key})

    if not data:
        return await message.reply("❌ Invalid key")

    if data["expires"] < datetime.utcnow():
        return await message.reply("❌ Key expired")

    await bot.send_message(
        data["owner"],
        f"🔔 **Key Activated**\nUser ID: `{message.from_user.id}`"
    )

    await message.reply("✅ Key is live, your user ID sent")

# ───────────────── Admin ───────────────── #
@Bot.on_message(filters.command("ongenkey") & filters.private)
async def ongen(bot, message):
    if message.from_user.id not in ADMINS:
        return
    settings_collection.update_one({"_id": "genkey"}, {"$set": {"enabled": True}})
    await message.reply("✅ /genkey Enabled")

@Bot.on_message(filters.command("offgenkey") & filters.private)
async def offgen(bot, message):
    if message.from_user.id not in ADMINS:
        return
    settings_collection.update_one({"_id": "genkey"}, {"$set": {"enabled": False}})
    await message.reply("❌ /genkey Disabled")

# ───────────────── Group Auto Delete ───────────────── #
@Bot.on_message(filters.group & ~filters.service)
async def group_filter(bot, message):
    text = message.text or ""

    if re.search(r"\d{5,}", text):
        return
    if re.search(r"(http|https).*tvkurl\.site", text):
        return

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
