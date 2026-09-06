# database.py (UPDATED VERSION)

import motor.motor_asyncio
from pymongo.errors import DuplicateKeyError
from config import Config

class Database:
    def __init__(self):
        self._client = None
        self.db = None
        self.collection = None
        if not Config.DATABASE_URL:
            print("WARNING: DATABASE_URL not set. Links will not be permanent.")

    async def connect(self):
        """Database se connection banata hai."""
        if Config.DATABASE_URL:
            print("Connecting to the database...")
            self._client = motor.motor_asyncio.AsyncIOMotorClient(Config.DATABASE_URL)
            self.db = self._client["StreamLinksDB"]
            self.collection = self.db["links"]
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

    async def get_link(self, unique_id):
        if self.collection is not None:
            doc = await self.collection.find_one({'_id': unique_id})
            return doc.get('message_id') if doc else None
        return None

db = Database()
