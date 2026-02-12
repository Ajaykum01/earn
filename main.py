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

def gen_token(n=10):
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

# ───────── /GENLINK (GROUP + PRIVATE) ───────── #
@Bot.on_message(filters.command("genlink"))
async def genlink(bot, m):

    ensure_user(m.from_user.id)

    user_data = users.find_one({"_id": m.from_user.id}) or {}
    last_gen = user_data.get("last_gen")
    now = datetime.utcnow()

    # 1 HOUR COOLDOWN
    if last_gen:
        remaining = timedelta(hours=1) - (now - last_gen)
        if remaining.total_seconds() > 0:
            minutes = int(remaining.total_seconds() // 60)
            return await m.reply(
                f"⏳ Wait {minutes} minutes before generating next link."
            )

    token = gen_token()

    rewards.insert_one({
        "token": token,
        "user": m.from_user.id,
        "used": False,
        "created_at": now
    })

    users.update_one(
        {"_id": m.from_user.id},
        {"$set": {"last_gen": now}},
        upsert=True
    )

    me = await bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=reward_{token}"
    short_link = shorten(deep_link)

    await m.reply(
        "💰 Your ₹1.5 Reward Link\n"
        "⏳ Valid 60 minutes\n\n"
        "Complete the short link to earn.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open Link", url=short_link)]
        ])
    )

# ───────── /START + CLAIM ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):

    ensure_user(m.from_user.id)

    if len(m.command) > 1 and m.command[1].startswith("reward_"):

        token = m.command[1].split("_")[1]
        data = rewards.find_one({"token": token})

        if not data:
            return await m.reply("❌ Invalid token.")

        if data.get("used"):
            return await m.reply("❌ Token already used.")

        if data.get("user") != m.from_user.id:
            return await m.reply("❌ This link is not yours.")

        if datetime.utcnow() - data["created_at"] > timedelta(hours=1):
            return await m.reply("❌ Token expired.")

        rewards.update_one(
            {"token": token},
            {"$set": {"used": True}}
        )

        users.update_one(
            {"_id": m.from_user.id},
            {"$inc": {"wallet": 1.5}}
        )

        return await m.reply("✅ ₹1.5 added to your wallet!")

    await m.reply(
        "👋 Welcome!\n\n"
        "Use /genlink to generate earning link.\n"
        "Cooldown: 1 hour"
    )

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

# ───────── WITHDRAW SWITCH ───────── #
@Bot.on_message(filters.command("onwithdraw") & filters.private)
async def onwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(True)
    await m.reply("✅ Withdraw ENABLED")

@Bot.on_message(filters.command("offwithdraw") & filters.private)
async def offwithdraw(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin only.")
    set_withdraw(False)
    await m.reply("❌ Withdraw DISABLED")

# ───────── /UPIID ───────── #
@Bot.on_message(filters.command("upiid") & filters.private)
async def upiid(bot, m):

    try:
        upi, amt = m.command[1], int(m.command[2])
    except:
        return await m.reply("Usage: /upiid name@upi 100")

    ok, reason = can_withdraw(m.from_user.id, amt)
    if not ok:
        return await m.reply(reason)

    wid = gen_token()

    withdraws.insert_one({
        "_id": wid,
        "user": m.from_user.id,
        "amount": amt,
        "upi": upi,
        "status": "pending",
        "date": datetime.utcnow()
    })

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{wid}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{wid}")
        ]
    ])

    await bot.send_message(
        ADMIN_CHANNEL,
        f"💸 Withdraw Request\n"
        f"User: {m.from_user.id}\n"
        f"Amount: ₹{amt}\n"
        f"UPI: {upi}",
        reply_markup=buttons
    )

    await m.reply("✅ Request sent to admin.")

# ───────── APPROVE / REJECT ───────── #
@Bot.on_callback_query(filters.regex("^approve_"))
async def approve(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})

    if not data or data["status"] != "pending":
        return await q.answer("Invalid")

    users.update_one(
        {"_id": data["user"]},
        {"$inc": {"wallet": -data["amount"]}}
    )

    withdraws.update_one(
        {"_id": wid},
        {"$set": {"status": "approved"}}
    )

    await bot.send_message(data["user"], "✅ Withdraw Approved")
    await q.message.edit_text(q.message.text + "\n\n✅ APPROVED")

@Bot.on_callback_query(filters.regex("^reject_"))
async def reject(bot, q):
    wid = q.data.split("_")[1]
    withdraws.update_one(
        {"_id": wid},
        {"$set": {"status": "rejected"}}
    )
    await q.message.edit_text(q.message.text + "\n\n❌ REJECTED")

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
    print("🚀 Bot Running")
    Bot.run()
