import os
import threading
import random
import string
import asyncio
import aiohttp
import urllib.parse
import re
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

FORCE_SUB_LINKS = [
    "https://t.me/+wMO973O29JEyNzRl",
    "https://t.me/freefirepanellinks",
]

# ───────────────── Helpers ───────────────── #
def ensure_user(user_id: int):
    if not users_collection.find_one({"_id": user_id}):
        users_collection.insert_one({"_id": user_id})

def gen_key(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def is_joined(bot, user_id):
    try:
        for link in FORCE_SUB_LINKS:
            chat = link.split("/")[-1]
            member = await bot.get_chat_member(chat, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        return True
    except:
        return False

# ───────────────── /start ───────────────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, message):
    user_id = message.from_user.id
    ensure_user(user_id)

    if not await is_joined(bot, user_id):
        buttons = [[InlineKeyboardButton("Join Channel", url=url)] for url in FORCE_SUB_LINKS]
        buttons.append([InlineKeyboardButton("Verify ✅", callback_data="verify_join")])
        return await message.reply(
            "🚫 **Join all channels to use this bot**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    await message.reply(
        "✅ **Bot is Alive**\n\n"
        "**Commands:**\n"
        "/start – Check bot\n"
        "/setinfo <user_id>\n"
        "/genkey\n"
        "/key <KEY>"
    )

@Bot.on_callback_query(filters.regex("^verify_join$"))
async def verify_join(bot, query):
    if await is_joined(bot, query.from_user.id):
        await query.message.edit_text("✅ Verified! Use /start")
    else:
        await query.answer("❌ Join all channels first", show_alert=True)

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
async def on_genkey(bot, message):
    if message.from_user.id not in ADMINS:
        return
    settings_collection.update_one({"_id": "genkey"}, {"$set": {"enabled": True}})
    await message.reply("✅ /genkey Enabled")

@Bot.on_message(filters.command("offgenkey") & filters.private)
async def off_genkey(bot, message):
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
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is Alive!")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

# ───────────────── Main ───────────────── #
if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    Bot.run(workers=1)
