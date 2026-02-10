import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MONGO_URL = os.getenv("MONGO_URL")
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL")
ADMINS = [int(x) for x in os.getenv("ADMINS").split()]
