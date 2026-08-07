# app/database.py
from motor.motor_asyncio import AsyncIOMotorClient
import os
from app.core.config import settings
from urllib.parse import quote_plus

MONGODB_URI = settings.db.url
MONGODB_DB = settings.db
if MONGODB_URI:
    # 例如: mongodb://user:pass@127.0.0.1:27017/facerecapi?authSource=admin
    client = AsyncIOMotorClient(MONGODB_URI)
    # 自动从 URI 里取库名；若 URI 没带路径，回退 MONGO_DB
    from pymongo.uri_parser import parse_uri
    parsed = parse_uri(MONGODB_URI)
    dbname = (parsed.get("database") or os.getenv("MONGO_DB", "facerecapi"))
else:
    USER = quote_plus(MONGODB_DB.username)
    PASS = quote_plus(MONGODB_DB.password)
    HOST = MONGODB_DB.host
    PORT = MONGODB_DB.port
    DB   = MONGODB_DB.database
    AUTH = MONGODB_DB.auth_source  # admin


    uri = f"mongodb://{USER}:{PASS}@{HOST}:{PORT}/{DB}?authSource={AUTH}"
    client = AsyncIOMotorClient(uri)
    dbname = DB

db = client.get_database(dbname)

def get_session():
    return db
