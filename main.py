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
WITHDRAW_CHANNEL = int(os.getenv("WITHDRAW_CHANNEL", "-1003624079737"))
TVK_API_TOKEN = "9986767adc94f9d0a46a66fe436a9ba577c74f1f"

# ===== Database =====
mongo = MongoClient(MONGO_URL)
db = mongo["earn_bot"]
users = db["users"]
reward_tokens = db["reward_tokens"]
gift_codes = db["gift_codes"]
withdraw_requests = db["withdraw_requests"]
settings = db["settings"]

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


@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(_, message):
    ensure_user(message.from_user.id)

    if len(message.command) > 1 and message.command[1].startswith("reward_"):
        token = message.command[1].split("reward_", 1)[1].strip()
        data = reward_tokens.find_one({"token": token})

        if not data:
            return await message.reply("❌ Invalid link.")
        if data.get("used"):
            return await message.reply("❌ This link is already used.")
        if data.get("owner_id") != message.from_user.id:
            return await message.reply("❌ This link is only for the user who generated it.")

        reward_tokens.update_one({"_id": data["_id"]}, {"$set": {"used": True, "used_at": datetime.utcnow()}})
        users.update_one({"_id": message.from_user.id}, {"$inc": {"wallet": 1.5}})

        return await message.reply("✅ Reward claimed! ₹1.5 added to your wallet.")

    await message.reply(
        "👋 Welcome to Earn Bot\n\n"
        "Available Commands:\n"
        "/start - Show welcome message\n"
        "/genlink - Generate earning link\n"
        "/wallet - Check your balance\n"
        "/withdraw - Withdraw your money\n"
        "/redeemgift CODE - Redeem gift code"
    )


@bot.on_message(filters.command("genlink") & (filters.group | filters.private))
async def cmd_genlink(client, message):
    ensure_user(message.from_user.id)

    user = users.find_one({"_id": message.from_user.id})
    last_gen = user.get("last_gen")

    if time_gap_enabled() and last_gen and datetime.utcnow() - last_gen < timedelta(hours=1):
        wait_left = timedelta(hours=1) - (datetime.utcnow() - last_gen)
        mins = int(wait_left.total_seconds() // 60)
        return await message.reply(f"⏳ Wait {mins} minutes before generating a new link.")

    token = new_code(10)
    reward_tokens.insert_one(
        {
            "token": token,
            "owner_id": message.from_user.id,
            "used": False,
            "created_at": datetime.utcnow(),
        }
    )
    users.update_one({"_id": message.from_user.id}, {"$set": {"last_gen": datetime.utcnow()}})

    me = await client.get_me()
    deep_link = f"https://t.me/{me.username}?start=reward_{token}"
    short_link = shorten_tvk(deep_link)

    await message.reply(
        "🔗 Your earning link is ready!\n"
        "• Reward: ₹1.5 (one-time use)\n"
        "• Only you can claim this link\n\n"
        f"{short_link}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open Link", url=short_link)]]
        ),
        disable_web_page_preview=True,
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
    await message.reply(
        f"💸 Withdraw Options\n"
        f"Your Balance: ₹{fmt_money(balance)}\n\n"
        "Use one of these:\n"
        "/upiid yourupi@bank amount\n"
        "/gmail yourmail@gmail.com amount\n\n"
        "Example:\n"
        "/upiid abcd@axl 100\n"
        "/gmail abcd@gmail.com 100"
    )


async def create_withdraw_request(message, method: str, account: str, amount_raw: str):
    if not withdraw_enabled():
        return await message.reply("❌ Withdraw is currently disabled.")

    ensure_user(message.from_user.id)

    try:
        amount = float(amount_raw)
    except ValueError:
        return await message.reply("❌ Invalid amount.")

    if amount <= 0:
        return await message.reply("❌ Amount must be greater than 0.")

    user = users.find_one({"_id": message.from_user.id})
    balance = float(user.get("wallet", 0))
    if balance < amount:
        return await message.reply(f"❌ Insufficient balance. Your balance: ₹{fmt_money(balance)}")

    request_id = new_code(12)
    withdraw_requests.insert_one(
        {
            "request_id": request_id,
            "user_id": message.from_user.id,
            "method": method,
            "account": account,
            "amount": amount,
            "status": "pending",
            "created_at": datetime.utcnow(),
        }
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve:{request_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject:{request_id}"),
            ]
        ]
    )

    await bot.send_message(
        WITHDRAW_CHANNEL,
        "💳 New Withdraw Request\n"
        f"Request ID: {request_id}\n"
        f"User ID: {message.from_user.id}\n"
        f"Method: {method}\n"
        f"Account: {account}\n"
        f"Amount: ₹{fmt_money(amount)}",
        reply_markup=keyboard,
    )

    await message.reply("✅ Withdraw request sent for admin review.")


@bot.on_message(filters.command("upiid") & filters.private)
async def cmd_upiid(_, message):
    if len(message.command) < 3:
        return await message.reply("Usage: /upiid yourupi@bank amount")
    await create_withdraw_request(message, "UPI", message.command[1], message.command[2])


@bot.on_message(filters.command("gmail") & filters.private)
async def cmd_gmail(_, message):
    if len(message.command) < 3:
        return await message.reply("Usage: /gmail yourmail@gmail.com amount")
    await create_withdraw_request(message, "GMAIL", message.command[1], message.command[2])


@bot.on_callback_query(filters.regex(r"^wd_(approve|reject):"))
async def withdraw_action(_, query):
    if not query.from_user or not is_admin(query.from_user.id):
        return await query.answer("Admins only.", show_alert=True)

    action, request_id = query.data.split(":", 1)
    req = withdraw_requests.find_one({"request_id": request_id})

    if not req:
        return await query.answer("Request not found.", show_alert=True)

    if req.get("status") != "pending":
        return await query.answer("Already processed.", show_alert=True)

    if action == "wd_approve":
        user = users.find_one({"_id": req["user_id"]})
        balance = float(user.get("wallet", 0))

        if balance < float(req["amount"]):
            withdraw_requests.update_one(
                {"_id": req["_id"]},
                {"$set": {"status": "rejected", "reason": "insufficient_balance_on_approval", "updated_at": datetime.utcnow()}},
            )
            await bot.send_message(req["user_id"], "❌ Withdraw rejected: insufficient balance.")
            await query.message.edit_text(query.message.text + "\n\n⚠️ Auto-rejected (insufficient balance).")
            return await query.answer("Auto-rejected.")

        users.update_one({"_id": req["user_id"]}, {"$inc": {"wallet": -float(req["amount"])}})
        withdraw_requests.update_one(
            {"_id": req["_id"]},
            {"$set": {"status": "approved", "updated_at": datetime.utcnow()}},
        )
        await bot.send_message(req["user_id"], f"✅ Your withdraw request was approved. ₹{fmt_money(req['amount'])} deducted.")
        await query.message.edit_text(query.message.text + f"\n\n✅ Approved by {query.from_user.mention}.")
        return await query.answer("Approved")

    withdraw_requests.update_one(
        {"_id": req["_id"]},
        {"$set": {"status": "rejected", "updated_at": datetime.utcnow()}},
    )
    await bot.send_message(req["user_id"], "❌ Your withdraw request was rejected.")
    await query.message.edit_text(query.message.text + f"\n\n❌ Rejected by {query.from_user.mention}.")
    await query.answer("Rejected")


@bot.on_message(filters.command("gengift") & filters.private)
async def cmd_gengift(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")

    if len(message.command) < 3:
        return await message.reply("Usage: /gengift amount qty")

    try:
        amount = float(message.command[1])
        qty = int(message.command[2])
    except ValueError:
        return await message.reply("❌ Invalid amount or qty.")

    if amount <= 0 or qty <= 0:
        return await message.reply("❌ Amount and qty must be greater than 0.")

    created = []
    for _ in range(qty):
        code = new_code(8) + "FRE"
        gift_codes.insert_one(
            {
                "code": code,
                "amount": amount,
                "used": False,
                "used_by": None,
                "created_at": datetime.utcnow(),
            }
        )
        created.append(code)

    await message.reply("🎁 Gift code(s):\n" + "\n".join(created))


@bot.on_message(filters.command("redeemgift") & filters.private)
async def cmd_redeemgift(_, message):
    ensure_user(message.from_user.id)

    if len(message.command) < 2:
        return await message.reply("Usage: /redeemgift CODE")

    code = message.command[1].strip().upper()
    gift = gift_codes.find_one({"code": code})

    if not gift:
        return await message.reply("❌ Invalid gift code.")
    if gift.get("used"):
        return await message.reply("❌ This gift code is already redeemed.")

    gift_codes.update_one(
        {"_id": gift["_id"]},
        {"$set": {"used": True, "used_by": message.from_user.id, "used_at": datetime.utcnow()}},
    )
    users.update_one({"_id": message.from_user.id}, {"$inc": {"wallet": float(gift["amount"])}})

    await message.reply(f"✅ Gift redeemed! ₹{fmt_money(gift['amount'])} added to your wallet.")


@bot.on_message(filters.command("onwithdraw") & filters.private)
async def cmd_onwithdraw(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": True}}, upsert=True)
    await message.reply("✅ Withdraw is ON for all users.")


@bot.on_message(filters.command("offwithdraw") & filters.private)
async def cmd_offwithdraw(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "withdraw"}, {"$set": {"enabled": False}}, upsert=True)
    await message.reply("✅ Withdraw is OFF for all users.")


@bot.on_message(filters.command("ontime") & filters.private)
async def cmd_ontime(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "time_gap"}, {"$set": {"enabled": True}}, upsert=True)
    await message.reply("✅ /genlink 1-hour cooldown is ON.")


@bot.on_message(filters.command("offtime") & filters.private)
async def cmd_offtime(_, message):
    if not is_admin(message.from_user.id):
        return await message.reply("❌ Admin only.")
    settings.update_one({"_id": "time_gap"}, {"$set": {"enabled": False}}, upsert=True)
    await message.reply("✅ /genlink 1-hour cooldown is OFF.")


# ===== Health endpoint for Koyeb =====
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
