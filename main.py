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
from pyrogram.errors import FloodWait

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
if not settings.find_one({"_id": "withdraw"}):
    settings.insert_one({"_id": "withdraw", "enabled": True})
if not settings.find_one({"_id": "time_gap"}):
    settings.insert_one({"_id": "time_gap", "enabled": True})


def ensure_user(user_id: int) -> None:
    if not users.find_one({"_id": user_id}):
        users.insert_one({"_id": user_id, "wallet": 0.0, "last_gen": None})


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def withdraw_enabled() -> bool:
    row = settings.find_one({"_id": "withdraw"})
    return bool(row and row.get("enabled", False))


def time_gap_enabled() -> bool:
    row = settings.find_one({"_id": "time_gap"})
    return bool(row and row.get("enabled", False))


def new_code(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def shorten_tvk(url: str) -> str:
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
    except Exception:
        return url


def fmt_money(amount: float) -> str:
    return ("{:.2f}".format(amount)).rstrip("0").rstrip(".")


bot = Client("earn-bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# ===== Auto Delete Handler =====
@bot.on_message(filters.group & ~filters.service)
async def auto_delete_handler(client, message):
    if not message.from_user:
        return
    if is_admin(message.from_user.id):
        return
    me = await client.get_me()
    if message.from_user.id == me.id:
        return
    
    # Logic: if it's NOT a genlink command, delete it
    if message.text:
        cmd = message.text.lower()
        if cmd.startswith("/genlink"):
            return

    try:
        await message.delete()
    except Exception:
        pass


@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(_, message):
    ensure_user(message.from_user.id)

    if len(message.command) > 1 and message.command[1].startswith("reward_"):
        token = message.command[1].split("reward_", 1)[1].strip()
        data = reward_tokens.find_one({"token": token})

        if not data:
            return await message.reply("❌ Invalid token.")

        # Ownership Protection
        if data.get("owner_id") != message.from_user.id:
            return await message.reply("❌ This is not your token. Only the person who generated it can claim it.")

        if data.get("used"):
            return await message.reply("❌ This token is already used.")

        reward_tokens.update_one({"_id": data["_id"]}, {"$set": {"used": True, "used_at": datetime.utcnow()}})
        users.update_one({"_id": message.from_user.id}, {"$inc": {"wallet": 1.5}})

        return await message.reply("✅ Reward claimed! ₹1.5 added to your wallet.")

    await message.reply("👋 Welcome! Use /genlink in the group to earn.")


@bot.on_message(filters.command("genlink"))
async def cmd_genlink(client, message):
    # This now handles both Private and Group because filters.command is broad
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
        f"🔗 **Link Generated!**\n👤 **User:** {message.from_user.mention}\n\n"
        f"Only you can claim this reward.\n\n"
        f"👉 {short_link}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Claim ₹1.5", url=short_link)]]),
        disable_web_page_preview=True
    )


@bot.on_message(filters.command("wallet") & filters.private)
async def cmd_wallet(_, message):
    ensure_user(message.from_user.id)
    balance = users.find_one({"_id": message.from_user.id}).get("wallet", 0)
    await message.reply(f"💰 Your wallet balance: ₹{fmt_money(balance)}")


@bot.on_message(filters.command("withdraw") & filters.private)
async def cmd_withdraw(_, message):
    if not withdraw_enabled():
        return await message.reply("❌ Withdraw is currently disabled.")
    ensure_user(message.from_user.id)
    balance = users.find_one({"_id": message.from_user.id}).get("wallet", 0)
    await message.reply(f"💸 Your Balance: ₹{fmt_money(balance)}\n\nUse `/upiid ID amount` or `/gmail MAIL amount` to withdraw.")

async def create_withdraw_request(message, method: str, account: str, amount_raw: str):
    if not withdraw_enabled(): return
    ensure_user(message.from_user.id)
    try:
        amount = float(amount_raw)
    except:
        return await message.reply("❌ Invalid amount.")
    
    user = users.find_one({"_id": message.from_user.id})
    if user.get("wallet", 0) < amount:
        return await message.reply("❌ Insufficient balance.")

    users.update_one({"_id": message.from_user.id}, {"$inc": {"wallet": -amount}})
    request_id = new_code(12)
    withdraw_requests.insert_one({
        "request_id": request_id, "user_id": message.from_user.id, "method": method,
        "account": account, "amount": amount, "status": "pending", "created_at": datetime.utcnow()
    })
    
    text = f"💳 **Withdraw Request**\nUser: {message.from_user.id}\nAmount: ₹{amount}\nMethod: {method}\nAcc: {account}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve:{request_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject:{request_id}")]])
    
    await bot.send_message(WITHDRAW_CHANNEL, text, reply_markup=kb)
    await message.reply("✅ Request sent to admin.")


@bot.on_message(filters.command("upiid") & filters.private)
async def cmd_upiid(_, message):
    if len(message.command) < 3: return
    await create_withdraw_request(message, "UPI", message.command[1], message.command[2])

@bot.on_message(filters.command("gmail") & filters.private)
async def cmd_gmail(_, message):
    if len(message.command) < 3: return
    await create_withdraw_request(message, "GMAIL", message.command[1], message.command[2])

@bot.on_callback_query(filters.regex(r"^wd_"))
async def wd_cb(_, query):
    if not is_admin(query.from_user.id): return
    action, rid = query.data.split(":")
    req = withdraw_requests.find_one({"request_id": rid})
    if not req or req["status"] != "pending": return
    
    if "approve" in action:
        withdraw_requests.update_one({"request_id": rid}, {"$set": {"status": "approved"}})
        await bot.send_message(req["user_id"], "✅ Withdrawal Approved!")
    else:
        users.update_one({"_id": req["user_id"]}, {"$inc": {"wallet": req["amount"]}})
        withdraw_requests.update_one({"request_id": rid}, {"$set": {"status": "rejected"}})
        await bot.send_message(req["user_id"], "❌ Withdrawal Rejected. Refunded.")
    await query.message.edit_text(query.message.text + "\n\nDONE")

@bot.on_message(filters.command("redeemgift") & filters.private)
async def cmd_redeemgift(_, message):
    ensure_user(message.from_user.id)
    if len(message.command) < 2: return
    code = message.command[1].upper()
    gift = gift_codes.find_one({"code": code, "used": False})
    if not gift: return await message.reply("❌ Invalid code.")
    gift_codes.update_one({"_id": gift["_id"]}, {"$set": {"used": True}})
    users.update_one({"_id": message.from_user.id}, {"$inc": {"wallet": gift["amount"]}})
    await message.reply(f"✅ Redeemed ₹{gift['amount']}")

@bot.on_message(filters.command(["onwithdraw", "offwithdraw", "ontime", "offtime"]) & filters.private)
async def admin_cmds(_, message):
    if not is_admin(message.from_user.id): return
    cmd = message.command[0]
    target = "withdraw" if "withdraw" in cmd else "time_gap"
    val = "on" in cmd
    settings.update_one({"_id": target}, {"$set": {"enabled": val}}, upsert=True)
    await message.reply(f"✅ {target} is now {val}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever(), daemon=True).start()
    bot.run()
