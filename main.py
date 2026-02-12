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
from datetime import datetime

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
# The specific group ID where the bot should function
ALLOWED_GROUP_ID = -1002341851502 # Extracted from your link

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
        users.insert_one({"_id": uid, "wallet": 0, "last_gen": None})

def withdraw_enabled():
    s = settings.find_one({"_id": "withdraw"})
    return s.get("enabled", False)

def gen_token(n=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def shorten(url):
    try:
        api = f"https://tvkurl.site/api?api=9986767adc94f9d0a46a66fe436a9ba577c74f1f&url={urllib.parse.quote_plus(url)}"
        return urllib.request.urlopen(api).read().decode().strip()
    except:
        return url

# ───────── AUTO CLEANER (DELETE NON-ADMIN MSGS) ───────── #
@Bot.on_message(filters.group & ~filters.service)
async def cleaner(bot, m):
    # Only run in the specified group
    if m.chat.id != ALLOWED_GROUP_ID:
        return

    # Check if sender is Admin or the Bot itself
    is_admin = False
    if m.from_user:
        if m.from_user.id in ADMINS:
            is_admin = True
        else:
            try:
                # Check if user is a group admin via chat member status
                member = await bot.get_chat_member(m.chat.id, m.from_user.id)
                if member.status in ["administrator", "creator"]:
                    is_admin = True
            except Exception:
                pass

    # If it's not an admin and not the bot, delete the message
    me = await bot.get_me()
    if not is_admin and m.from_user.id != me.id:
        try:
            await m.delete()
        except Exception:
            pass

# ───────── GENLINK (GROUP ONLY) ───────── #
@Bot.on_message(filters.command("genlink") & filters.group)
async def genlink(bot, m):
    if m.chat.id != ALLOWED_GROUP_ID:
        return
    
    ensure_user(m.from_user.id)
    
    # Logic: Generate token and link without cooldown
    token = gen_token(10)
    # Store token in DB to verify later
    rewards.insert_one({"uid": m.from_user.id, "token": token, "claimed": False})
    
    # Generate the earning link
    me = await bot.get_me()
    raw_url = f"https://t.me/{me.username}?start=verify_{token}"
    short_url = shorten(raw_url)
    
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Click to Earn", url=short_url)]])
    
    await m.reply(
        f"👤 User: {m.from_user.mention}\n"
        f"🔗 Your link is ready. Click below to earn ₹5!",
        reply_markup=btn
    )
    
    # Deletes the command message
    try:
        await m.delete()
    except:
        pass

# ───────── START / VERIFY (PRIVATE ONLY) ───────── #
@Bot.on_message(filters.command("start") & filters.private)
async def start(bot, m):
    ensure_user(m.from_user.id)
    
    if len(m.command) > 1 and m.command[1].startswith("verify_"):
        token = m.command[1].split("_")[1]
        data = rewards.find_one({"token": token, "claimed": False})
        
        if data:
            if data["uid"] == m.from_user.id:
                users.update_one({"_id": m.from_user.id}, {"$inc": {"wallet": 5}})
                rewards.update_one({"token": token}, {"$set": {"claimed": True}})
                await m.reply("✅ Success! ₹5 added to your wallet.")
            else:
                await m.reply("❌ This link belongs to someone else.")
        else:
            await m.reply("❌ Invalid or already used link.")
        return

    await m.reply("👋 Welcome to Earn Bot!\n\nUse /wallet to check balance.\nUse /withdraw to cash out.")

# ───────── WALLET & WITHDRAW (PRIVATE) ───────── #
@Bot.on_message(filters.command("wallet") & filters.private)
async def wallet(bot, m):
    ensure_user(m.from_user.id)
    user_data = users.find_one({"_id": m.from_user.id})
    bal = user_data["wallet"] if user_data else 0
    status = "🟢 ENABLED" if withdraw_enabled() else "🔴 DISABLED"
    await m.reply(f"💰 Balance: ₹{bal}\n\nWithdraw Status: {status}\nMin Withdraw: ₹100")

@Bot.on_message(filters.command("withdraw") & filters.private)
async def withdraw(bot, m):
    await m.reply("💸 Withdraw via UPI:\nUsage: `/upiid name@upi amount`", parse_mode="markdown")

@Bot.on_message(filters.command("upiid") & filters.private)
async def upiid(bot, m):
    try:
        upi, amt = m.command[1], int(m.command[2])
    except:
        return await m.reply("Usage: /upiid name@upi 100")

    if not withdraw_enabled():
        return await m.reply("❌ Withdraw is currently disabled.")
    
    user_data = users.find_one({"_id": m.from_user.id})
    bal = user_data["wallet"] if user_data else 0
    
    if amt < 100:
        return await m.reply("❌ Minimum ₹100 required.")
    if bal < amt:
        return await m.reply("❌ Insufficient balance.")

    wid = gen_token()
    withdraws.insert_one({"_id": wid, "user": m.from_user.id, "amount": amt, "status": "pending"})
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{wid}"), 
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{wid}")]
    ])
    
    await bot.send_message(
        ADMIN_CHANNEL, 
        f"💸 New Request\nUser: `{m.from_user.id}`\nAmount: ₹{amt}\nUPI: `{upi}`", 
        reply_markup=buttons
    )
    await m.reply("✅ Request sent to admin.")

# ───────── ADMIN CALLBACKS ───────── #
@Bot.on_callback_query(filters.regex("^approve_"))
async def approve(bot, q):
    wid = q.data.split("_")[1]
    data = withdraws.find_one({"_id": wid})
    if not data or data["status"] != "pending": return
    
    users.update_one({"_id": data["user"]}, {"$inc": {"wallet": -data["amount"]}})
    withdraws.update_one({"_id": wid}, {"$set": {"status": "approved"}})
    
    try: 
        await bot.send_message(data["user"], "✅ Your withdraw has been Approved!") 
    except: 
        pass
    await q.message.edit_text(q.message.text + "\n\n✅ APPROVED")

@Bot.on_callback_query(filters.regex("^reject_"))
async def reject(bot, q):
    wid = q.data.split("_")[1]
    withdraws.update_one({"_id": wid}, {"$set": {"status": "rejected"}})
    await q.message.edit_text(q.message.text + "\n\n❌ REJECTED")

# ───────── SERVER ───────── #
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_server():
    HTTPServer(("0.0.0.0", 8080), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    print("Bot Started...")
    Bot.run()
