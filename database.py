# database.py (UPDATED VERSION)

import motor.motor_asyncio
from pymongo.errors import DuplicateKeyError
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from config import Config

SERVICE_TIMEZONE = ZoneInfo("Asia/Kolkata")

class Database:
    def __init__(self):
        self._client = None
        self.db = None
        self.collection = None
        self.users = None
        if not Config.DATABASE_URL:
            print("WARNING: DATABASE_URL not set. Links will not be permanent.")

    async def connect(self):
        """Database se connection banata hai."""
        database_url = Config.DATABASE_URL.strip()
        if database_url and not database_url.startswith(("mongodb://", "mongodb+srv://")):
            print("WARNING: DATABASE_URL is not a valid MongoDB URI. Continuing without database persistence.")
            database_url = ""

        if database_url:
            print("Connecting to the database...")
            self._client = motor.motor_asyncio.AsyncIOMotorClient(database_url)
            self.db = self._client["StreamLinksDB"]
            self.collection = self.db["links"]
            self.users = self.db["users"]
            await self.collection.create_index(
                [("source_chat_id", 1), ("source_message_id", 1)],
                unique=True,
                partialFilterExpression={
                    "source_chat_id": {"$exists": True},
                    "source_message_id": {"$exists": True},
                },
                name="unique_source_upload",
            )
            print("✅ Database connection established.")
        else:
            self.db = None
            self.collection = None
            self.users = None

    async def disconnect(self):
        """Database connection ko band karta hai."""
        if self._client:
            self._client.close()
            print("Database connection closed.")

    async def save_link(self, unique_id, message_id):
        if self.collection is not None:
            await self.collection.insert_one({'_id': unique_id, 'message_id': message_id})

    async def reserve_link(self, unique_id, source_chat_id, source_message_id):
        """Reserve one source Telegram message so duplicate updates are ignored."""
        if self.collection is None:
            return True, None

        try:
            await self.collection.insert_one(
                {
                    "_id": unique_id,
                    "source_chat_id": source_chat_id,
                    "source_message_id": source_message_id,
                    "status": "processing",
                }
            )
            return True, None
        except DuplicateKeyError:
            existing = await self.collection.find_one(
                {
                    "source_chat_id": source_chat_id,
                    "source_message_id": source_message_id,
                }
            )
            return False, existing

    async def complete_link(self, unique_id, message_id):
        if self.collection is not None:
            await self.collection.update_one(
                {"_id": unique_id},
                {"$set": {"message_id": message_id, "status": "ready"}},
            )

    async def release_link(self, unique_id):
        if self.collection is not None:
            await self.collection.delete_one(
                {"_id": unique_id, "status": "processing"}
            )

    async def reserve_daily_quota(self, user_id, daily_limit=3):
        """Reserve one free link slot, or allow paid users without a limit."""
        if self.users is None:
            return True, False

        now = datetime.now(timezone.utc)
        quota_day = datetime.now(SERVICE_TIMEZONE).strftime("%Y-%m-%d")
        await self.users.update_one(
            {"_id": user_id},
            {"$setOnInsert": {"quota_day": quota_day, "daily_count": 0}},
            upsert=True,
        )
        user = await self.users.find_one({"_id": user_id}) or {}
        paid_until = user.get("paid_until")
        if paid_until and paid_until.tzinfo is None:
            paid_until = paid_until.replace(tzinfo=timezone.utc)
        if paid_until and paid_until > now:
            return True, True

        if user.get("quota_day") != quota_day:
            result = await self.users.update_one(
                {"_id": user_id, "quota_day": {"$ne": quota_day}},
                {"$set": {"quota_day": quota_day, "daily_count": 1}},
            )
            return result.modified_count == 1, False

        result = await self.users.update_one(
            {"_id": user_id, "quota_day": quota_day, "daily_count": {"$lt": daily_limit}},
            {"$inc": {"daily_count": 1}},
        )
        return result.modified_count == 1, False

    async def release_daily_quota(self, user_id):
        """Return a reserved free slot when link creation fails."""
        if self.users is None:
            return
        quota_day = datetime.now(SERVICE_TIMEZONE).strftime("%Y-%m-%d")
        await self.users.update_one(
            {"_id": user_id, "quota_day": quota_day, "daily_count": {"$gt": 0}},
            {"$inc": {"daily_count": -1}},
        )

    async def add_subscription(self, user_id, days):
        """Extend a user's paid access and return the new expiry timestamp."""
        if self.users is None:
            return None
        now = datetime.now(timezone.utc)
        user = await self.users.find_one({"_id": user_id}) or {}
        current_until = user.get("paid_until")
        if current_until and current_until.tzinfo is None:
            current_until = current_until.replace(tzinfo=timezone.utc)
        start_at = current_until if current_until and current_until > now else now
        paid_until = start_at + timedelta(days=days)
        await self.users.update_one(
            {"_id": user_id},
            {"$set": {"paid_until": paid_until}},
            upsert=True,
        )
        return paid_until

    async def get_subscription(self, user_id):
        """Return the user's active subscription expiry, if any."""
        if self.users is None:
            return None
        user = await self.users.find_one({"_id": user_id}, {"paid_until": 1})
        paid_until = user.get("paid_until") if user else None
        if paid_until and paid_until.tzinfo is None:
            paid_until = paid_until.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return paid_until if paid_until and paid_until > now else None

    async def get_link(self, unique_id):
        if self.collection is not None:
            doc = await self.collection.find_one({'_id': unique_id})
            return doc.get('message_id') if doc else None
        return None

    async def list_ready_links(self, limit=100):
        """Return link records that can be shown in the browser catalog."""
        if self.collection is None:
            return []

        cursor = self.collection.find(
            {"message_id": {"$exists": True}},
            {"_id": 1, "message_id": 1},
        ).sort("_id", -1).limit(limit)
        return await cursor.to_list(length=limit)

db = Database()
