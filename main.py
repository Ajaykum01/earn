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
from datetime import datetime

load_dotenv()

# ───────────────── MongoDB ───────────────── #
MONGO_URL = os.getenv("MONGO_URL")
client = MongoClient(MONGO_URL)
db = client["telegram_bot"]

users_collection = db["users"]

# ───────────────── Bot ───────────────── #
Bot = Client(
    "Play-Store-Bot",
    bot_token=os.environ["BOT_TOKEN"],
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"]
)

# Channels to SHOW (no checking)
JOIN_BUTTONS = [
    ("Join Channel 1", "https://t.me/+bTtZvd_xMrU5NWI1"),
    ("Join Channel 2", "https://t.me/freefirepanellinks"),
    ("Join Channel 3", "https://t.me/+wMO973O29JEyNzRl")
]

# ───────────────── Helpers ───────────────── #
def ensure_user(user_id: int):
    if not users_collection.find_one({"_id": user_id}):
        users_collection.insert_one({
            "_id": user_id,
            "joined_at": datetime.utcnow()
        })

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
        "You can now use the bot."
    )

# ───────────────── Group Auto Delete ───────────────── #
@Bot.on_message(filters.group & ~filters.service)
async def group_filter(bot, message):
    text = message.text or ""

    # Allow numbers or tvkurl links only
    if re.search(r"\d{5,}", text):
        return
    if re.search(r"(http|https).*tvkurl\.site", text):
        return

    try:
        await message.delete()
    except:
        pass

# ───────────────── Delete Join Messages ───────────────── #
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
