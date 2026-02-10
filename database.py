from pymongo import MongoClient
from config import MONGO_URL

client = MongoClient(MONGO_URL)
db = client["tg_key_bot"]

users = db.users
keys = db.keys
settings = db.settings

if not settings.find_one({"_id": "genkey"}):
    settings.insert_one({"_id": "genkey", "enabled": True})
