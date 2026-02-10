import re
import secrets
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import *
from database import users, keys, settings

app = Client(
    "KeyBot",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH
)

# ───────── Force Join Check ───────── #
async def is_joined(bot, user_id):
    try:
        m = await bot.get_chat_member(FORCE_CHANNEL, user_id)
        return m.status in ["member", "administrator", "creator"]
    except:
        return False

# ───────── /start ───────── #
@app.on_message(filters.command("start") & filters.private)
async def start(bot, msg):
    if not await is_joined(bot, msg.from_user.id):
        await msg.reply(
            "🚫 **Join channel to use this bot**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Join Channel", url=f"https://t.me/{FORCE_CHANNEL.replace('@','')}")],
                [InlineKeyboardButton("🔁 Verify", callback_data="verify")]
            ])
        )
        return

    await msg.reply(
        "✅ **Bot is Alive**\n\n"
        "**Commands:**\n"
        "/start\n"
        "/setinfo <user_id>\n"
        "/genkey\n"
        "/key <KEY>"
    )

@app.on_callback_query(filters.regex("verify"))
async def verify(bot, cb):
    if await is_joined(bot, cb.from_user.id):
        await cb.message.edit_text("✅ Verified! Use /start")
    else:
        await cb.answer("❌ Join channel first", show_alert=True)

# ───────── /setinfo ───────── #
@app.on_message(filters.command("setinfo") & filters.private)
async def setinfo(_, msg):
    if len(msg.command) != 2 or not msg.command[1].isdigit():
        return await msg.reply("Usage: `/setinfo 585227752`")

    users.update_one(
        {"_id": msg.from_user.id},
        {"$set": {"target_id": msg.command[1]}},
        upsert=True
    )
    await msg.reply("✅ User ID saved")

# ───────── /genkey ───────── #
@app.on_message(filters.command("genkey") & filters.private)
async def genkey(_, msg):
    state = settings.find_one({"_id": "genkey"})
    if not state["enabled"]:
        return await msg.reply(
            "❌ **GenKey OFF**\n\n"
            "⏰ Available timings:\n"
            "9am–10am\n12pm–1pm\n6pm–7pm\n9pm–10pm"
        )

    key = secrets.token_hex(4)
    keys.insert_one({
        "key": key,
        "owner": msg.from_user.id,
        "expires": datetime.utcnow() + timedelta(days=1)
    })

    await msg.reply(f"🔑 **Your Key:** `{key}`\n⏳ Valid 24 hours")

# ───────── /key ───────── #
@app.on_message(filters.command("key") & filters.private)
async def use_key(bot, msg):
    if len(msg.command) != 2:
        return await msg.reply("Usage: `/key ABC123`")

    data = keys.find_one({"key": msg.command[1]})
    if not data:
        return await msg.reply("❌ Invalid key")

    if data["expires"] < datetime.utcnow():
        return await msg.reply("❌ Key expired")

    await bot.send_message(
        data["owner"],
        f"🔔 **Key Activated**\nUser ID: `{msg.from_user.id}`"
    )

    await msg.reply("✅ Key live, your user ID sent")

# ───────── Admin Controls ───────── #
@app.on_message(filters.command("ongenkey") & filters.user(ADMINS))
async def ongen(_, msg):
    settings.update_one({"_id": "genkey"}, {"$set": {"enabled": True}})
    await msg.reply("✅ GenKey Enabled")

@app.on_message(filters.command("offgenkey") & filters.user(ADMINS))
async def offgen(_, msg):
    settings.update_one({"_id": "genkey"}, {"$set": {"enabled": False}})
    await msg.reply("❌ GenKey Disabled")

# ───────── Group Auto-Delete Logic ───────── #
@app.on_message(filters.group & ~filters.service)
async def group_filter(_, msg):
    text = msg.text or ""

    allow_number = re.search(r"\d{5,}", text)
    allow_link = re.search(r"(http|https).*tvkurl\.site", text)

    if allow_number or allow_link:
        return

    await msg.delete()

app.run()
