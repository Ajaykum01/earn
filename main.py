import os
import threading
import random
import string
import urllib.parse
import urllib.request
import asyncio
import pytz
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
settings = db["settings"]

ADMIN_CHANNEL = int(os.getenv("ADMIN_CHANNEL"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split()]

IST = pytz.timezone("Asia/Kolkata")

if not settings.find_one({"_id": "wallet"}):
    settings.insert_one({"_id": "wallet", "enabled": False})

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

def wallet_enabled():
    return settings.find_one({"_id": "wallet"})["enabled"]

def is_withdraw_day():
    now = datetime.now(IST)
    return now.day in [1, 2]

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

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
    await m.reply("👋 Welcome! Use /wallet to see earnings.")

# ───────── GROUP LOCK ───────── #
@Bot.on_message(filters.group & ~filters.command("genlink"))
async def delete_other(bot, m):
    if m.from_user and m.from_user.id in ADMINS:
        return
    try:
        await m.delete()
    except:
        pass

# ───────── GENLINK ───────── #
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
    short = shorten(f"https://t.me/{me.username}?start=reward_{token}")

    msg = await m.reply(
        "💰 Here is your ₹5 Key Token",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Open Link", url=short)]])
    )

    asyncio.create_task(auto_delete(msg, 1200))

# ───────── CLAIM ₹5 ───────── #
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

        return await m.reply("✅ ₹5 added!")

# ───────── WALLET ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    bal = users.find_one({"_id": m.from_user.id})["wallet"]
    status = "🟢 ENABLED" if wallet_enabled() else "🔴 DISABLED"

    await m.reply(
        f"💰 Balance: ₹{bal}\n\n"
        f"Withdraw Status: {status}\n"
        f"Withdraw Window: 1st – 2nd Every Month\n"
        f"Minimum Withdraw: ₹100"
    )

# ───────── GIFT GENERATION (ADMIN) ───────── #
@Bot.on_message(filters.command("gengift") & filters.private)
async def gengift(bot, m):
    if m.from_user.id not in ADMINS:
        return await m.reply("❌ Admin Only")

    amount = int(m.command[1])
    count = int(m.command[2])

    codes = []

    for _ in range(count):
        code = gen_token()
        giftcodes.insert_one({"code": code, "amount": amount, "used": False})
        codes.append(code)

    await m.reply("🎁 Gift Codes Generated:\n\n" + "\n".join(codes))

# ───────── REDEEM GIFT ───────── #
@Bot.on_message(filters.command("redeemgift") & filters.private)
async def redeemgift(bot, m):
    code = m.command[1]

    gift = giftcodes.find_one({"code": code})

    if not gift or gift["used"]:
        return await m.reply("❌ Invalid or Used Code")

    giftcodes.update_one({"code": code}, {"$set": {"used": True}})
    users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": gift["amount"]}})

    await m.reply(f"🎉 ₹{gift['amount']} added to your wallet!")

# ───────── ADMIN SWITCH ───────── #
@Bot.on_message(filters.command("onwallet") & filters.private)
async def onwallet(bot, m):
    if m.from_user.id in ADMINS:
        settings.update_one({"_id": "wallet"}, {"$set": {"enabled": True}})
        await m.reply("✅ Withdraw ENABLED")

@Bot.on_message(filters.command("offwallet") & filters.private)
async def offwallet(bot, m):
    if m.from_user.id in ADMINS:
        settings.update_one({"_id": "wallet"}, {"$set": {"enabled": False}})
        await m.reply("❌ Withdraw DISABLED")

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
