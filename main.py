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
WITHDRAW_CHANNEL = -1003624079737  # FIXED CHANNEL
TVK_API_TOKEN = "9986767adc94f9d0a46a66fe436a9ba577c74f1f"

# ===== Database =====
mongo = MongoClient(MONGO_URL)
db = mongo["earn_bot"]
users = db["users"]
reward_tokens = db["reward_tokens"]
gift_codes = db["gift_codes"]
withdraw_requests = db["withdraw_requests"]
settings = db["settings"]

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

def fmt_money(amount: float):
    return ("{:.2f}".format(amount)).rstrip("0").rstrip(".")


bot = Client("earn-bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# ================== WITHDRAW REQUEST ==================
async def create_withdraw_request(message, method, account, amount_raw):

    if not withdraw_enabled():
        return await message.reply("❌ Withdraw is disabled.")

    ensure_user(message.from_user.id)

    try:
        amount = float(amount_raw)
    except:
        return await message.reply("❌ Invalid amount.")

    user = users.find_one({"_id": message.from_user.id})
    balance = float(user.get("wallet", 0))

    if balance < amount:
        return await message.reply("❌ Insufficient balance.")

    # 💰 DEDUCT IMMEDIATELY
    users.update_one({"_id": message.from_user.id}, {"$inc": {"wallet": -amount}})

    request_id = new_code(10)

    withdraw_requests.insert_one({
        "request_id": request_id,
        "user_id": message.from_user.id,
        "method": method,
        "account": account,
        "amount": amount,
        "status": "pending",
        "created_at": datetime.utcnow(),
    })

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve:{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject:{request_id}")
        ]]
    )

    await bot.send_message(
        WITHDRAW_CHANNEL,
        f"""💳 Withdraw Request

User: {message.from_user.id}
Method: {method}
Account: {account}
Amount: ₹{fmt_money(amount)}""",
        reply_markup=keyboard
    )

    await message.reply("✅ Withdraw request sent to admin.")

# ================== CALLBACK ==================
@bot.on_callback_query(filters.regex("^wd_"))
async def withdraw_action(_, query):

    if not is_admin(query.from_user.id):
        return await query.answer("Admins only", show_alert=True)

    action, request_id = query.data.split(":", 1)
    req = withdraw_requests.find_one({"request_id": request_id})

    if not req or req["status"] != "pending":
        return await query.answer("Already processed", show_alert=True)

    if action == "wd_approve":
        withdraw_requests.update_one(
            {"_id": req["_id"]},
            {"$set": {"status": "approved"}}
        )

        await bot.send_message(
            req["user_id"],
            f"✅ Withdraw approved ₹{fmt_money(req['amount'])}"
        )

        await query.message.edit_text(query.message.text + "\n\n✅ Approved")
        return await query.answer("Approved")

    # ❌ Reject → Add money back
    users.update_one(
        {"_id": req["user_id"]},
        {"$inc": {"wallet": float(req["amount"])}}
    )

    withdraw_requests.update_one(
        {"_id": req["_id"]},
        {"$set": {"status": "rejected"}}
    )

    await bot.send_message(
        req["user_id"],
        f"❌ Withdraw rejected ₹{fmt_money(req['amount'])} returned to wallet"
    )

    await query.message.edit_text(query.message.text + "\n\n❌ Rejected")
    await query.answer("Rejected")

# ================== COMMANDS ==================
@bot.on_message(filters.command("upiid") & filters.private)
async def cmd_upiid(_, message):
    if len(message.command) < 3:
        return await message.reply("Usage: /upiid yourupi amount")
    await create_withdraw_request(message, "UPI", message.command[1], message.command[2])

@bot.on_message(filters.command("gmail") & filters.private)
async def cmd_gmail(_, message):
    if len(message.command) < 3:
        return await message.reply("Usage: /gmail mail@gmail.com amount")
    await create_withdraw_request(message, "GMAIL", message.command[1], message.command[2])

@bot.on_message(filters.command("ontime") & filters.private)
async def cmd_ontime(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "time_gap"}, {"$set": {"enabled": True}}, upsert=True)
    await message.reply("✅ 1-hour cooldown enabled.")

@bot.on_message(filters.command("offtime") & filters.private)
async def cmd_offtime(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "time_gap"}, {"$set": {"enabled": False}}, upsert=True)
    await message.reply("✅ 1-hour cooldown disabled.")

@bot.on_message(filters.command("onwithdraw") & filters.private)
async def cmd_onwithdraw(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": True}}, upsert=True)
    await message.reply("✅ Withdraw ON.")

@bot.on_message(filters.command("offwithdraw") & filters.private)
async def cmd_offwithdraw(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": False}}, upsert=True)
    await message.reply("✅ Withdraw OFF.")

# ===== Health Server =====
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
