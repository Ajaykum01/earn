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
giftcodes = db["giftcodes"]

ADMIN_CHANNEL = int(os.getenv("ADMIN_CHANNEL"))
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
        users.insert_one({"_id": uid, "wallet": 0, "last_gen": None})

def gen_token(n=10):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=n))

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

# ───────── START ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)

    await m.reply("👋 Welcome!\nUse /wallet to check balance.")

# ───────── GROUP LOCK (ONLY /genlink) ───────── #
@Bot.on_message(filters.group & ~filters.command("genlink"))
async def delete_other(bot, m):
    if m.from_user and m.from_user.id in ADMINS:
        return
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
        return await m.reply("⏳ Wait 2hr30min before generating again.")

    token = gen_token()
    rewards.insert_one({"token": token, "user": uid, "used": False})

    users.update_one({"_id": uid}, {"$set": {"last_gen": datetime.utcnow()}})

    me = await bot.get_me()
    deep = f"https://t.me/{me.username}?start=reward_{token}"
    short = shorten(deep)

    msg = await m.reply(
        "💰 Here is your ₹5 Key Token",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open Link", url=short)],
            [InlineKeyboardButton("❓ How to Open", url="https://t.me")]
        ])
    )

    asyncio.create_task(auto_delete(msg, 1200))

# ───────── CLAIM REWARD ───────── #
@Bot.on_message(filters.private & filters.command("start"))
async def claim(bot, m):
    ensure_user(m.from_user.id)

    if len(m.command) > 1 and m.command[1].startswith("reward_"):
        token = m.command[1].split("_")[1]
        r = rewards.find_one({"token": token})

        if not r or r["used"] or r["user"] != m.from_user.id:
            return await m.reply("❌ Invalid or used.")

        rewards.update_one({"token": token}, {"$set": {"used": True}})
        users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 5}})

        return await m.reply("✅ ₹5 added to wallet!")

# ───────── WALLET (PRIVATE ONLY) ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    bal = users.find_one({"_id": m.from_user.id})["wallet"]
    await m.reply(f"💰 Balance: ₹{bal}")

# ───────── WITHDRAW MENU ───────── #
@Bot.on_message(filters.command("withdraw") & filters.private)
async def withdraw(bot, m):
    await m.reply(
        "Select Withdraw:\n\n"
        "UPI → /upiid abc@upi 50\n"
        "Redeem → /gmail mail@gmail.com 50"
    )

# ───────── WITHDRAW REQUEST ───────── #
async def send_admin(text, wid):
    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{wid}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{wid}")
        ]
    ])
    await Bot.send_message(ADMIN_CHANNEL, text, reply_markup=btn)

@Bot.on_message(filters.command("upiid") & filters.private)
async def upi(bot, m):
    upi, amt = m.command[1], int(m.command[2])
    wid = gen_token()

    withdraws.insert_one({"_id": wid, "user": m.from_user.id, "amount": amt, "status": "pending"})
    await send_admin(f"UPI Withdraw\nUser:{m.from_user.id}\nUPI:{upi}\n₹{amt}", wid)
    await m.reply("✅ Sent for approval.")

@Bot.on_message(filters.command("gmail") & filters.private)
async def gmail(bot, m):
    mail, amt = m.command[1], int(m.command[2])
    wid = gen_token()

    withdraws.insert_one({"_id": wid, "user": m.from_user.id, "amount": amt, "status": "pending"})
    await send_admin(f"Redeem\nUser:{m.from_user.id}\nMail:{mail}\n₹{amt}", wid)
    await m.reply("✅ Sent for approval.")

# ───────── APPROVE / REJECT ───────── #
@Bot.on_callback_query(filters.regex("^approve_"))
async def approve(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})

    if data and data["status"] == "pending":
        users.update_one({"_id": data["user"]}, {"$inc": {"wallet": -data["amount"]}})
        withdraws.update_one({"_id": wid}, {"$set": {"status": "approved"}})
        await bot.send_message(data["user"], "✅ Withdraw Approved")

@Bot.on_callback_query(filters.regex("^reject_"))
async def reject(bot, q):
    wid = q.data.split("_")[1]
    withdraws.update_one({"_id": wid}, {"$set": {"status": "rejected"}})

# ───────── GIFT SYSTEM ───────── #
@Bot.on_message(filters.command("gengift") & filters.private)
async def gengift(bot, m):
    if m.from_user.id not in ADMINS:
        return

    amt, count = int(m.command[1]), int(m.command[2])

    codes = []
    for _ in range(count):
        c = gen_token(8)
        giftcodes.insert_one({"code": c, "amount": amt, "used": False})
        codes.append(c)

    await m.reply("\n".join(codes))

@Bot.on_message(filters.command("redeemgift") & filters.private)
async def redeemgift(bot, m):
    code = m.command[1]
    g = giftcodes.find_one({"code": code})

    if not g or g["used"]:
        return await m.reply("Invalid or used code.")

    giftcodes.update_one({"code": code}, {"$set": {"used": True}})
    users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": g["amount"]}})
    await m.reply(f"🎁 ₹{g['amount']} added!")

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
