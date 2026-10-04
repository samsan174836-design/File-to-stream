# app.py (THE REAL, FINAL, CLEAN, EASY-TO-READ FULL CODE)

import os
import asyncio
import secrets
import time
import traceback
import uvicorn
import re
import logging
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pyrogram import Client, filters, enums
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from pyrogram.errors import FloodWait, UserNotParticipant
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from pyrogram.file_id import FileId
from pyrogram import raw
from pyrogram.session import Session, Auth
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import math

# Project ki dusri files se important cheezein import karo
from config import Config
from database import db

# =====================================================================================
# --- SETUP: BOT, WEB SERVER, AUR LOGGING ---
# =====================================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Yeh function bot ko web server ke saath start aur stop karta hai.
    """
    print("--- Lifespan: Server chalu ho raha hai... ---")
    
    await db.connect()
    
    global bot_ready, startup_error, startup_retry_task
    bot_ready = False
    startup_error = None
    startup_retry_task = None
    await start_bot_with_retry()
    
    yield
    
    print("--- Lifespan: Server band ho raha hai... ---")
    if startup_retry_task:
        startup_retry_task.cancel()
        try:
            await startup_retry_task
        except asyncio.CancelledError:
            pass
        startup_retry_task = None
    pending_client_retries = list(client_retry_tasks.values())
    for task in pending_client_retries:
        task.cancel()
    if pending_client_retries:
        await asyncio.gather(*pending_client_retries, return_exceptions=True)
    client_retry_tasks.clear()
    for client_id, client in list(multi_clients.items()):
        if client is bot or not client.is_initialized:
            continue
        try:
            await client.stop()
        except Exception as e:
            print(f"Warning: Additional bot {client_id} shutdown failed: {type(e).__name__}")
    if bot.is_initialized:
        try:
            await bot.stop()
        except Exception as e:
            print(f"Warning: Bot shutdown failed: {e}")
    await db.disconnect()
    print("--- Lifespan: Shutdown poora hua. ---")

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- LOG FILTER: YEH SIRF /dl/ WALE LOGS KO CHUPAYEGA ---
class HideDLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Agar log message mein "GET /dl/" hai, toh usse mat dikhao
        return "GET /dl/" not in record.getMessage()

# Uvicorn ke 'access' logger par filter lagao
logging.getLogger("uvicorn.access").addFilter(HideDLFilter())
# --- FIX KHATAM ---

bot = Client("SimpleStreamBot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN, in_memory=True)
multi_clients = {}; work_loads = {}; class_cache = {}; client_retry_tasks = {}
bot_ready = False
startup_error = None
startup_retry_task = None
recent_uploads = {}
upload_lock = asyncio.Lock()
upload_delay_seconds = float(os.environ.get("UPLOAD_DELAY_SECONDS", "1.5"))
FREE_DAILY_LINK_LIMIT = 3
MEDIA_MESSAGE_CACHE_TTL_SECONDS = 60
media_message_cache = {}
media_message_fetches = {}

# =====================================================================================
# --- MULTI-CLIENT LOGIC ---
# =====================================================================================

class TokenParser:
    """Parse numbered additional bot tokens from environment variables."""
    @staticmethod
    def parse_from_env():
        configured_tokens = []
        for name in sorted(os.environ):
            if name == "MULTI_TOKEN":
                token_index = 1
            else:
                match = re.fullmatch(r"MULTI_TOKEN_?([1-9]\d*)", name)
                if not match:
                    continue
                token_index = int(match.group(1))
            token = os.environ.get(name, "").strip()
            if token:
                configured_tokens.append((token_index, name, token))

        configured_tokens.sort(key=lambda item: (item[0], item[1]))
        seen_tokens = {Config.BOT_TOKEN.strip()} if Config.BOT_TOKEN.strip() else set()
        parsed_tokens = {}
        for _, name, token in configured_tokens:
            if token in seen_tokens:
                print(f"Skipping duplicate bot token configured in {name}.")
                continue
            parsed_tokens[len(parsed_tokens) + 1] = token
            seen_tokens.add(token)
        return parsed_tokens


async def _fetch_media_message(client, message_id, cache_key):
    try:
        message = await client.get_messages(Config.STORAGE_CHANNEL, message_id)
        if message and not message.empty:
            now = time.monotonic()
            for key, (expires_at, _) in list(media_message_cache.items()):
                if expires_at <= now:
                    media_message_cache.pop(key, None)
            if len(media_message_cache) >= 512:
                media_message_cache.pop(next(iter(media_message_cache)), None)
            media_message_cache[cache_key] = (
                now + MEDIA_MESSAGE_CACHE_TTL_SECONDS,
                message,
            )
        return message
    finally:
        if media_message_fetches.get(cache_key) is asyncio.current_task():
            media_message_fetches.pop(cache_key, None)


async def get_media_message(client, message_id):
    """Reuse short-lived Telegram metadata across a file's range requests."""
    cache_key = (id(client), str(Config.STORAGE_CHANNEL), int(message_id))
    cached = media_message_cache.get(cache_key)
    if cached:
        expires_at, message = cached
        if expires_at > time.monotonic():
            return message
        media_message_cache.pop(cache_key, None)

    fetch = media_message_fetches.get(cache_key)
    if fetch is None:
        fetch = asyncio.create_task(
            _fetch_media_message(client, message_id, cache_key)
        )
        media_message_fetches[cache_key] = fetch
    return await asyncio.shield(fetch)


async def retry_additional_client(client_id, bot_token, wait_seconds):
    current_task = asyncio.current_task()
    try:
        await asyncio.sleep(wait_seconds)
        if client_retry_tasks.get(client_id) is current_task:
            client_retry_tasks.pop(client_id, None)
        existing_client = multi_clients.get(client_id)
        if not existing_client or not existing_client.is_connected:
            await start_client(client_id, bot_token)
    except asyncio.CancelledError:
        raise
    finally:
        if client_retry_tasks.get(client_id) is current_task:
            client_retry_tasks.pop(client_id, None)


async def start_client(client_id, bot_token):
    """Start an additional download bot without exposing its token in logs."""
    existing_client = multi_clients.get(client_id)
    if existing_client and existing_client.is_connected:
        return True

    try:
        print(f"Attempting to start additional bot {client_id}.")
        client = Client(
            name=str(client_id),
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=bot_token,
            no_updates=False,
            in_memory=True,
        )
        register_additional_bot_handlers(client)
        client = await client.start()
        work_loads[client_id] = 0
        multi_clients[client_id] = client
        print(f"Additional bot {client_id} started successfully.")
        return True
    except FloodWait as error:
        wait_seconds = max(int(error.value) + 1, 1)
        print(
            f"Additional bot {client_id} hit Telegram FloodWait; "
            f"retrying in {wait_seconds} seconds."
        )
        retry_task = client_retry_tasks.get(client_id)
        if retry_task is None or retry_task.done():
            client_retry_tasks[client_id] = asyncio.create_task(
                retry_additional_client(client_id, bot_token, wait_seconds)
            )
        return False
    except Exception as error:
        print(
            f"Additional bot {client_id} failed to start "
            f"({type(error).__name__}). Check its token and channel access."
        )
        return False

async def initialize_clients():
    """Start every valid optional bot configured in the environment."""
    all_tokens = TokenParser.parse_from_env()
    if not all_tokens:
        print("No additional clients found. Using default bot only.")
        return
    
    print(f"Found {len(all_tokens)} additional bot token(s). Starting them...")
    results = await asyncio.gather(
        *(start_client(client_id, token) for client_id, token in all_tokens.items())
    )
    started_count = sum(results)
    print(
        f"Multi-client status: {started_count}/{len(all_tokens)} additional bot(s) "
        "started; the main bot remains available if an extra bot is unavailable."
    )

# =====================================================================================
# --- HELPER FUNCTIONS ---
# =====================================================================================

def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return '0B'
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB'}
    while size_in_bytes >= power and n < len(power_labels) - 1:
        size_in_bytes /= power
        n += 1
    return f"{size_in_bytes:.2f} {power_labels[n]}"

def mask_filename(name: str):
    if not name:
        return "Protected File"
    base, ext = os.path.splitext(name)
    metadata_pattern = re.compile(
        r'((19|20)\d{2}|4k|2160p|1080p|720p|480p|360p|HEVC|x265|BluRay|WEB-DL|HDRip)',
        re.IGNORECASE
    )
    match = metadata_pattern.search(base)
    if match:
        title_part = base[:match.start()].strip(' .-_')
        metadata_part = base[match.start():]
    else:
        title_part = base
        metadata_part = ""
    masked_title = ''.join(c if (i % 3 == 0 and c.isalnum()) else ('*' if c.isalnum() else c) for i, c in enumerate(title_part))
    return f"{masked_title} {metadata_part}{ext}".strip()

async def telegram_call_with_retry(operation, operation_name):
    """Retry Telegram operations when the API asks us to wait."""
    while True:
        try:
            return await operation()
        except FloodWait as error:
            wait_seconds = error.value + 1
            print(
                f"Telegram FloodWait during {operation_name}; "
                f"retrying in {wait_seconds}s."
            )
            await asyncio.sleep(wait_seconds)

# =====================================================================================
# --- PYROGRAM BOT HANDLERS ---
# =====================================================================================

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        unique_id = message.command[1].split("_", 1)[1]
        
        if Config.FORCE_SUB_CHANNEL:
            try:
                await client.get_chat_member(Config.FORCE_SUB_CHANNEL, user_id)
            except UserNotParticipant:
                channel_username = str(Config.FORCE_SUB_CHANNEL).replace('@', '')
                channel_link = f"https://t.me/{channel_username}"
                join_button = InlineKeyboardButton("📢 Join Channel", url=channel_link)
                bot_username = getattr(getattr(client, "me", None), "username", None) or Config.BOT_USERNAME
                retry_button = InlineKeyboardButton(
                    "✅ Joined",
                    url=f"https://t.me/{bot_username}?start={message.command[1]}",
                )
                keyboard = InlineKeyboardMarkup([[join_button], [retry_button]])
                await message.reply_text(
                    "**You Must Join Our Channel To Get The Link!**\n\n"
                    "__Join Channel & Click '✅ Joined'.__",
                    reply_markup=keyboard, quote=True
                )
                return

        final_link = f"{Config.BASE_URL}/show/{unique_id}"
        reply_text = f"__✅ Verification Successful!\n\nCopy Link:__ `{final_link}`"
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Open Link", url=final_link)]])
        await message.reply_text(reply_text, reply_markup=button, quote=True, disable_web_page_preview=True)

    else:
        paid_until = await db.get_subscription(user_id)
        if user_id == Config.OWNER_ID:
            reply_text = f"""
🛡️ <b>Owner Control Center</b>

Welcome back, <b>{escape(user_name)}</b>.

Your service is online and ready to manage. You can grant premium access with:
<code>/add user_id days</code>

Example: <code>/add 123456789 200</code>

📊 <b>Plans:</b> Free users get 3 links daily; premium users get unlimited links.
💳 <b>Premium:</b> ₹100/month

🔐 Use <code>/website</code> to open your private video library.
"""
        elif paid_until:
            days_left = max(1, math.ceil((paid_until - datetime.now(timezone.utc)).total_seconds() / 86400))
            expiry_text = paid_until.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
            reply_text = f"""
🌟 <b>Welcome back to Karva Bhaiya Premium, {escape(user_name)}!</b>

Thank you for supporting our service. Your premium access is active, so you can create unlimited streaming links without daily limits.

⏳ <b>Days remaining:</b> {days_left}
📅 <b>Valid until:</b> {expiry_text}

Send any video, audio, or document whenever you are ready. We appreciate your support! 🙏

🔐 Use <code>/website</code> anytime to open your private video library.
"""
        else:
            reply_text = f"""
👋 <b>Welcome to Karva Bhaiya, {escape(user_name)}!</b>

Your private media link assistant for fast, reliable streaming.

<b>How it works</b>
• Send or forward any video, audio, or document.
• Receive a secure streaming link in seconds.
• Open it on any device and continue watching anytime.

<b>Free plan:</b> 3 links every day
<b>Premium plan:</b> Unlimited links for ₹100/month

Send your first file whenever you are ready.
🔐 Use <code>/website</code> to securely open your private video library.
"""
        await message.reply_text(reply_text, parse_mode=enums.ParseMode.HTML)

@bot.on_message(filters.command("add") & filters.private)
async def add_subscription_command(_, message: Message):
    """Allow the owner to grant or extend a user's paid access."""
    if not message.from_user or message.from_user.id != Config.OWNER_ID:
        await message.reply_text(
            "⛔ <b>Access restricted.</b> This command is available only to the owner.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if len(message.command) != 3:
        await message.reply_text(
            "<b>Usage:</b> <code>/add user_id days</code>\n\n"
            "Example: <code>/add 123456789 200</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    try:
        target_user_id = int(message.command[1])
        days = int(message.command[2])
        if target_user_id <= 0 or days <= 0:
            raise ValueError
    except ValueError:
        await message.reply_text(
            "Please provide a valid user ID and a number of days greater than zero.",
            quote=True,
        )
        return

    paid_until = await db.add_subscription(target_user_id, days)
    if not paid_until:
        await message.reply_text(
            "⚠️ Subscription could not be saved because the database is unavailable.",
            quote=True,
        )
        return

    await message.reply_text(
        "✅ <b>Premium access updated</b>\n\n"
        f"User: <code>{target_user_id}</code>\n"
        f"Added: <b>{days} days</b>\n"
        f"Valid until: <b>{paid_until.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST')}</b>",
        parse_mode=enums.ParseMode.HTML,
        quote=True,
    )

@bot.on_message(filters.command("website") & filters.private)
async def website_login_command(_, message: Message):
    """Send a one-time secure link to the user's private web library."""
    token = await db.create_web_login_token(message.from_user.id)
    if not token or not Config.BASE_URL:
        await message.reply_text(
            "⚠️ Website login is temporarily unavailable. Please try again later.",
            quote=True,
        )
        return

    website_url = f"{Config.BASE_URL}/auth/telegram?token={quote(token)}"
    button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 Open My Private Library", url=website_url)]]
    )
    await message.reply_text(
        "🔐 <b>Your private library is ready</b>\n\n"
        "Use the button below to securely open your personal website library.\n"
        "Only videos created from your Telegram account will be shown.",
        reply_markup=button,
        parse_mode=enums.ParseMode.HTML,
        quote=True,
        disable_web_page_preview=True,
    )

async def handle_file_upload(message: Message, user_id: int, source_bot_id: int):
    unique_id = secrets.token_urlsafe(8)
    source_chat_id = message.chat.id if message.chat else user_id
    source_message_id = f"{source_bot_id}:{message.id}"
    source_key = f"{source_chat_id}:{source_message_id}"
    now = time.monotonic()
    for key, seen_at in list(recent_uploads.items()):
        if now - seen_at > 3600:
            recent_uploads.pop(key, None)
    if source_key in recent_uploads:
        print(f"Duplicate upload update ignored in process: {source_key}")
        return
    recent_uploads[source_key] = now
    reply_attempted = False
    quota_reserved = False
    paid_user = False

    try:
        reserved, _existing = await db.reserve_link(
            unique_id, source_chat_id, source_message_id
        )
        if not reserved:
            print(
                f"Duplicate upload update ignored: "
                f"{source_chat_id}/{source_message_id}"
            )
            return

        quota_reserved, paid_user = await db.reserve_daily_quota(
            user_id, FREE_DAILY_LINK_LIMIT
        )
        link_allowed = quota_reserved or paid_user

        media = message.document or message.video or message.audio
        original_name = media.file_name or "file"
        stream_link = f"{Config.BASE_URL}/show/{unique_id}"
        display_name = escape(original_name)
        escaped_stream_link = escape(stream_link, quote=True)
        storage_button = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🎬 Stream", url=stream_link)]]
        )
        stream_caption = (
            f"🎬 <b>Stream Link:</b> "
            f'<a href="{escaped_stream_link}">{escaped_stream_link}</a>'
        )
        original_caption = (message.caption or "").strip()
        while True:
            escaped_caption = escape(original_caption)
            storage_caption = (
                f"{escaped_caption}\n\n{stream_caption}"
                if escaped_caption
                else stream_caption
            )
            if len(storage_caption) <= 1024 or not original_caption:
                break
            original_caption = original_caption[:-64].rstrip()

        async with upload_lock:
            sent_message = await telegram_call_with_retry(
                lambda: message.copy(chat_id=Config.STORAGE_CHANNEL),
                "copying upload to storage",
            )
            try:
                await telegram_call_with_retry(
                    lambda: sent_message.edit_caption(
                        caption=storage_caption,
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=storage_button,
                    ),
                    "updating storage caption",
                )
            except Exception:
                await sent_message.delete()
                raise
            await db.complete_link(unique_id, sent_message.id)

            if link_allowed:
                reply_text = (
                    "✅ <b>Your link is ready</b>\n\n"
                    f"📁 <b>File:</b> {display_name}\n\n"
                    f"📊 <b>Size:</b> {get_readable_file_size(media.file_size)}\n\n"
                    f"🎬 <b>Stream link:</b> "
                    f'<a href="{escaped_stream_link}">{escaped_stream_link}</a>\n\n'
                    "⏳ <i>This link remains available while the file is stored.</i>"
                )
            else:
                reply_text = (
                    "⚠️ <b>Your daily free limit has been reached</b>\n\n"
                    "Your 3 free links for today have been used. "
                    "Your file was received and stored securely, but a new link is not included in the free plan.\n\n"
                    "✨ <b>Upgrade to Premium</b>\n"
                    "Create unlimited links for just <b>₹100/month</b>.\n\n"
                    "Please contact the service owner to activate your subscription."
                )
            reply_attempted = True
            reply_markup = storage_button if link_allowed else None
            await telegram_call_with_retry(
                lambda: message.reply_text(
                    reply_text,
                    reply_markup=reply_markup,
                    quote=True,
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.HTML,
                ),
                "sending upload link",
            )
            await asyncio.sleep(upload_delay_seconds)
    except Exception as e:
        await db.release_link(unique_id)
        if quota_reserved and not paid_user:
            await db.release_daily_quota(user_id)
        print(f"!!! ERROR: {traceback.format_exc()}")
        if not reply_attempted:
            recent_uploads.pop(source_key, None)
            try:
                await message.reply_text(
                    "Sorry, something went wrong while creating your links.",
                    quote=True,
                )
            except Exception:
                print(f"!!! ERROR: Failed to send upload error reply: {traceback.format_exc()}")

@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def file_handler(client: Client, message: Message):
    if not message.from_user:
        return
    await handle_file_upload(message, message.from_user.id, client.me.id)


def register_additional_bot_handlers(client: Client):
    """Attach the same user-facing commands and uploads to each added bot."""
    client.add_handler(
        MessageHandler(start_command, filters.command("start") & filters.private)
    )
    client.add_handler(
        MessageHandler(add_subscription_command, filters.command("add") & filters.private)
    )
    client.add_handler(
        MessageHandler(website_login_command, filters.command("website") & filters.private)
    )
    client.add_handler(
        MessageHandler(
            file_handler,
            filters.private & (filters.document | filters.video | filters.audio),
        )
    )

@bot.on_chat_member_updated(filters.chat(Config.STORAGE_CHANNEL))
async def simple_gatekeeper(c: Client, m_update: ChatMemberUpdated):
    try:
        if(m_update.new_chat_member and m_update.new_chat_member.status==enums.ChatMemberStatus.MEMBER):
            u=m_update.new_chat_member.user
            if u.id==Config.OWNER_ID or u.is_self: return
            print(f"Gatekeeper: Kicking {u.id}"); await c.ban_chat_member(Config.STORAGE_CHANNEL,u.id); await c.unban_chat_member(Config.STORAGE_CHANNEL,u.id)
    except Exception as e: print(f"Gatekeeper Error: {e}")

async def cleanup_channel(c: Client):
    print("Gatekeeper: Running cleanup..."); allowed={Config.OWNER_ID,c.me.id}
    try:
        async for m in c.get_chat_members(Config.STORAGE_CHANNEL):
            if m.user.id in allowed: continue
            if m.status in [enums.ChatMemberStatus.ADMINISTRATOR,enums.ChatMemberStatus.OWNER]: continue
            try: print(f"Cleanup: Kicking {m.user.id}"); await c.ban_chat_member(Config.STORAGE_CHANNEL,m.user.id); await asyncio.sleep(1)
            except FloodWait as e: await asyncio.sleep(e.value)
            except Exception as e: print(f"Cleanup Error: {e}")
    except Exception as e: print(f"Cleanup Error: {e}")


async def retry_bot_startup(wait_seconds):
    global startup_retry_task
    current_task = asyncio.current_task()
    try:
        await asyncio.sleep(wait_seconds)
        if not bot_ready:
            print("Telegram rate limit wait ended; retrying bot startup.")
            if startup_retry_task is current_task:
                startup_retry_task = None
            await start_bot_with_retry()
    except asyncio.CancelledError:
        raise
    finally:
        if startup_retry_task is current_task:
            startup_retry_task = None


async def start_bot_with_retry():
    global bot_ready, startup_error, startup_retry_task
    bot_ready = False
    try:
        if not bot.is_connected:
            print("Starting main Pyrogram bot...")
            await bot.start()

        me = bot.me or await bot.get_me()
        Config.BOT_USERNAME = me.username
        print(f"✅ Main Bot [@{Config.BOT_USERNAME}] safaltapoorvak start ho gaya.")

        multi_clients[0] = bot
        work_loads.setdefault(0, 0)
        await initialize_clients()

        print(f"Verifying storage channel ({Config.STORAGE_CHANNEL})...")
        await bot.get_chat(Config.STORAGE_CHANNEL)
        print("✅ Storage channel accessible hai.")

        if Config.FORCE_SUB_CHANNEL:
            try:
                print(f"Verifying force sub channel ({Config.FORCE_SUB_CHANNEL})...")
                await bot.get_chat(Config.FORCE_SUB_CHANNEL)
                print("✅ Force Sub channel accessible hai.")
            except Exception as error:
                print(f"!!! WARNING: Bot, Force Sub channel mein admin nahi hai. Error: {error}")

        try:
            await cleanup_channel(bot)
        except Exception as error:
            print(f"Warning: Channel cleanup fail ho gaya. Error: {error}")

        startup_error = None
        bot_ready = True
        print("--- Lifespan: Startup safaltapoorvak poora hua. ---")
    except FloodWait as error:
        wait_seconds = max(int(error.value) + 1, 1)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
        startup_error = (
            f"Telegram rate limit active. The app will retry automatically after "
            f"{retry_at.isoformat()} (wait {wait_seconds} seconds). "
            "Do not restart the dyno during this wait."
        )
        print(f"!!! BOT STARTUP WAITING: {startup_error}")
        if startup_retry_task is None or startup_retry_task.done():
            startup_retry_task = asyncio.create_task(retry_bot_startup(wait_seconds))
    except ValueError as error:
        if "Peer id invalid" in str(error):
            startup_error = (
                f"STORAGE_CHANNEL={Config.STORAGE_CHANNEL} is not a valid "
                "Pyrogram channel ID. Use the exact -100... ID, or use "
                "@public_channel_username instead."
            )
            print(f"!!! STORAGE CHANNEL CONFIGURATION ERROR: {startup_error}")
        else:
            startup_error = "Bot startup failed because of an invalid configuration value."
            print(f"!!! BOT STARTUP FAILED: {traceback.format_exc()}")
        multi_clients.clear()
        work_loads.clear()
    except Exception:
        startup_error = "Bot startup failed. Check the Heroku logs for the Telegram error."
        multi_clients.clear()
        work_loads.clear()
        print(f"!!! BOT STARTUP FAILED: {traceback.format_exc()}")

# =====================================================================================
# --- FASTAPI WEB SERVER ---
# =====================================================================================
 
@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Render the public landing page without changing individual video links."""
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"request": request, "bot_ready": bot_ready, "initial_view": "home"},
    )

@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(
        "static/manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )

@app.get("/service-worker.js", include_in_schema=False)
async def pwa_service_worker():
    return FileResponse(
        "static/service-worker.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )

@app.get("/home", response_class=HTMLResponse)
@app.get("/continue", response_class=HTMLResponse)
@app.get("/completed", response_class=HTMLResponse)
@app.get("/later", response_class=HTMLResponse)
@app.get("/today", response_class=HTMLResponse)
@app.get("/all-lecture", response_class=HTMLResponse)
async def library_section_page(request: Request):
    section = request.url.path.strip("/") or "home"
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"request": request, "bot_ready": bot_ready, "initial_view": section},
    )

@app.get("/yt/playlist/{playlist_id}", response_class=HTMLResponse)
async def youtube_playlist_page(request: Request, playlist_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", playlist_id):
        raise HTTPException(status_code=404, detail="Invalid YouTube playlist.")
    return templates.TemplateResponse(
        request=request,
        name="youtube.html",
        context={"request": request, "media_type": "playlist", "media_id": playlist_id},
    )

@app.get("/yt/{video_id}", response_class=HTMLResponse)
async def youtube_video_page(request: Request, video_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(status_code=404, detail="Invalid YouTube video.")
    return templates.TemplateResponse(
        request=request,
        name="youtube.html",
        context={"request": request, "media_type": "video", "media_id": video_id},
    )

@app.get("/health")
async def health_check():
    """JSON health endpoint for uptime monitors and deployment checks."""
    if not bot_ready:
        return JSONResponse(
            status_code=503,
            content={
                "status": "starting",
                "message": startup_error or "Bot is not ready yet.",
            },
        )
    return {"status": "ok", "message": "Server is healthy and running!"}

async def get_web_user_id(request: Request):
    """Resolve the authenticated Telegram user for private website APIs."""
    return await db.get_web_session_user(request.cookies.get("karva_session"))

@app.get("/auth/session", response_class=JSONResponse)
async def check_web_session(request: Request):
    return {"authenticated": await get_web_user_id(request) is not None}

@app.get("/auth/telegram")
async def telegram_web_login(token: str):
    user_id = await db.consume_web_login_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="This website login link is expired or already used.")
    session_id = await db.create_web_session(user_id)
    if not session_id:
        raise HTTPException(status_code=503, detail="Website login is unavailable while the database is offline.")

    response = RedirectResponse(url="/", status_code=303)
    secure_cookie = Config.BASE_URL.startswith("https://") if Config.BASE_URL else False
    response.set_cookie(
        "karva_session",
        session_id,
        max_age=3650 * 24 * 60 * 60,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
    )
    return response

@app.post("/auth/logout")
async def telegram_web_logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("karva_session")
    return response

def _youtube_api_get(endpoint: str, params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "key": Config.YOUTUBE_API_KEY})
    request = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/{endpoint}?{query}",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)

def _youtube_duration_seconds(duration: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    return hours * 3600 + minutes * 60 + seconds

def _youtube_is_short(duration: int, title: str, description: str) -> bool:
    return duration <= 180 or bool(
        re.search(r"(?:^|\s)#shorts\b", f"{title} {description}", re.IGNORECASE)
    )

@app.get("/api/youtube/search", response_class=JSONResponse)
async def search_youtube_videos(request: Request, q: str):
    """Search non-live YouTube videos and playlists, excluding Shorts."""
    if await get_web_user_id(request) is None:
        raise HTTPException(status_code=401, detail="Open the website from the Telegram bot to search videos.")
    query = q.strip()
    if not query or len(query) > 100:
        raise HTTPException(status_code=400, detail="Enter a search term of 1 to 100 characters.")
    if not Config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube search is not configured.")

    search_params = {
        "part": "snippet",
        "q": query,
        "maxResults": 50,
        "safeSearch": "strict",
    }
    try:
        video_results, playlist_results = await asyncio.gather(
            asyncio.to_thread(
                _youtube_api_get,
                "search",
                {**search_params, "type": "video"},
            ),
            asyncio.to_thread(
                _youtube_api_get,
                "search",
                {**search_params, "type": "playlist"},
            ),
        )
        candidates = []
        for item in video_results.get("items", []):
            video_id = item.get("id", {}).get("videoId", "")
            snippet = item.get("snippet", {})
            if (
                item.get("id", {}).get("kind") != "youtube#video"
                or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                or snippet.get("liveBroadcastContent", "none") != "none"
            ):
                continue
            candidates.append((video_id, snippet))

        playlist_candidates = []
        for item in playlist_results.get("items", []):
            playlist_id = item.get("id", {}).get("playlistId", "")
            if (
                item.get("id", {}).get("kind") == "youtube#playlist"
                and re.fullmatch(r"[A-Za-z0-9_-]{10,64}", playlist_id)
            ):
                playlist_candidates.append((playlist_id, item.get("snippet", {})))

        detail_tasks = []
        if candidates:
            detail_tasks.append(asyncio.to_thread(
                _youtube_api_get,
                "videos",
                {
                    "part": "contentDetails,snippet,liveStreamingDetails",
                    "id": ",".join(video_id for video_id, _ in candidates),
                    "maxResults": len(candidates),
                },
            ))
        if playlist_candidates:
            detail_tasks.append(asyncio.to_thread(
                _youtube_api_get,
                "playlists",
                {
                    "part": "contentDetails,snippet",
                    "id": ",".join(playlist_id for playlist_id, _ in playlist_candidates),
                    "maxResults": len(playlist_candidates),
                },
            ))
        detail_results = await asyncio.gather(*detail_tasks) if detail_tasks else []
    except urllib.error.HTTPError as error:
        logging.warning("YouTube Data API returned HTTP %s", error.code)
        raise HTTPException(status_code=502, detail="YouTube search is temporarily unavailable.") from error
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as error:
        logging.warning("YouTube Data API request failed: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="YouTube search is temporarily unavailable.") from error

    video_details = detail_results[0] if candidates else {"items": []}
    playlist_details = detail_results[1 if candidates else 0] if playlist_candidates else {"items": []}
    detail_by_id = {
        item.get("id"): item
        for item in video_details.get("items", [])
        if item.get("id") and not item.get("liveStreamingDetails")
        and item.get("snippet", {}).get("liveBroadcastContent", "none") == "none"
    }
    results = []
    for video_id, snippet in candidates:
        video = detail_by_id.get(video_id)
        if not video:
            continue
        duration = _youtube_duration_seconds(video.get("contentDetails", {}).get("duration", ""))
        video_snippet = video.get("snippet", {})
        if _youtube_is_short(
            duration,
            video_snippet.get("title", snippet.get("title", "")),
            video_snippet.get("description", ""),
        ):
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("high")
            or thumbnails.get("medium")
            or thumbnails.get("default")
            or {}
        ).get("url")
        results.append({
            "type": "video",
            "videoId": video_id,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "thumbnail": thumbnail,
            "duration": duration,
        })

    playlist_detail_by_id = {
        item.get("id"): item
        for item in playlist_details.get("items", [])
        if item.get("id")
    }
    for playlist_id, snippet in playlist_candidates:
        playlist = playlist_detail_by_id.get(playlist_id)
        if not playlist:
            continue
        playlist_snippet = playlist.get("snippet", snippet)
        thumbnails = playlist_snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("high")
            or thumbnails.get("medium")
            or thumbnails.get("default")
            or {}
        ).get("url")
        results.append({
            "type": "playlist",
            "playlistId": playlist_id,
            "title": playlist_snippet.get("title", ""),
            "channel": playlist_snippet.get("channelTitle", ""),
            "thumbnail": thumbnail,
            "itemCount": playlist.get("contentDetails", {}).get("itemCount", 0),
        })

    return {"videos": results}

def _catalog_title(file_name: str) -> str:
    """Keep the original filename visible while removing only its extension."""
    return os.path.splitext(file_name or "Untitled video")[0].strip() or "Untitled video"

def _catalog_category(file_name: str, mime_type: str) -> str:
    name = (file_name or "").lower()
    if any(word in name for word in ("lecture", "class", "lesson", "chapter", "course", "study")):
        return "Learning"
    if mime_type.startswith("audio/"):
        return "Audio"
    return "Entertainment"

async def _catalog_item(link, main_bot, recent_cutoff, semaphore):
    unique_id = str(link.get("_id", ""))
    message_id = link.get("message_id")
    if not unique_id or not message_id:
        return None
    try:
        async with semaphore:
            message = await get_media_message(main_bot, message_id)
        media = message.document or message.video or message.audio
        if not media:
            return None
        file_name = media.file_name or "Untitled video"
        mime_type = media.mime_type or "application/octet-stream"
        inferred_mime_type, _ = mimetypes.guess_type(file_name)
        message_date = message.date
        if message_date and message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=timezone.utc)
        created_at = message_date.isoformat() if message_date else None
        is_video = (
            bool(message.video)
            or mime_type.startswith("video/")
            or bool(inferred_mime_type and inferred_mime_type.startswith("video/"))
        )
        return {
            "id": unique_id,
            "title": _catalog_title(file_name),
            "fileName": file_name,
            "thumbnail": f"/thumbnail/{quote(unique_id)}",
            "videoUrl": f"/show/{quote(unique_id)}",
            "duration": getattr(media, "duration", None),
            "category": _catalog_category(file_name, mime_type),
            "subject": None,
            "lectureNumber": None,
            "createdAt": created_at,
            "isRecent": bool(message_date and message_date >= recent_cutoff),
            "isVideo": is_video,
            "mimeType": mime_type,
            "fileSize": media.file_size,
        }
    except Exception:
        logging.exception("Catalog item could not be loaded for link %s", unique_id)
        return None

@app.get("/api/catalog", response_class=JSONResponse)
async def get_catalog(request: Request):
    """Return only the authenticated Telegram user's private catalog."""
    user_id = await get_web_user_id(request)
    if user_id is None:
        return JSONResponse(
            status_code=401,
            content={"status": "auth_required", "message": "Open the website from the Telegram bot."},
        )
    if not bot_ready:
        return {"status": "starting", "videos": []}

    main_bot = multi_clients.get(0)
    if not main_bot:
        return {"status": "starting", "videos": []}

    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    semaphore = asyncio.Semaphore(8)
    items = await asyncio.gather(*(
        _catalog_item(link, main_bot, recent_cutoff, semaphore)
        for link in await db.list_ready_links(user_id=user_id)
    ))
    videos = [item for item in items if item]

    return {"status": "ok", "videos": videos}

@app.get("/thumbnail/{unique_id}")
async def stream_thumbnail(request: Request, unique_id: str):
    """Stream Telegram's actual media thumbnail when one is available."""
    message_id = await db.get_link(unique_id)
    if not message_id:
        raise HTTPException(status_code=404, detail="Link expired or invalid.")
    main_bot = multi_clients.get(0)
    if not main_bot:
        raise HTTPException(status_code=503, detail="Bot is not ready.")
    try:
        message = await get_media_message(main_bot, message_id)
        media = message.document or message.video or message.audio
        if not media:
            raise HTTPException(status_code=404, detail="Thumbnail not available.")

        thumb = None
        if getattr(media, "thumbs", None):
            thumb = media.thumbs[0]
        elif getattr(media, "thumb", None):
            thumb = media.thumb

        if not thumb:
            raise HTTPException(status_code=404, detail="Thumbnail not available.")

        thumbnail_file_id = FileId.decode(thumb.file_id)
        client_id = min(work_loads, key=work_loads.get)
        client = multi_clients.get(client_id)
        if not client:
            raise HTTPException(status_code=503, detail="Bot is not ready.")
        streamer = class_cache.get(client) or ByteStreamer(client)
        class_cache[client] = streamer
        size = thumb.file_size or 0
        if not size:
            raise HTTPException(status_code=404, detail="Thumbnail size is unavailable.")
        body = streamer.yield_file(thumbnail_file_id, client_id, 0, 0, size, 1, size)
        return StreamingResponse(
            body,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception:
        logging.exception("Thumbnail could not be loaded for link %s", unique_id)
        raise HTTPException(status_code=404, detail="Thumbnail not available.")

@app.get("/show/{unique_id}", response_class=HTMLResponse)
async def show_page(request: Request, unique_id: str):
    if not bot_ready:
        raise HTTPException(
            status_code=503,
            detail=startup_error or "Bot is not ready yet. Please try again shortly.",
        )
    return templates.TemplateResponse(
        request=request,
        name="show.html",
        context={"request": request}
    )

@app.get("/api/file/{unique_id}", response_class=JSONResponse)
async def get_file_details_api(request: Request, unique_id: str):
    message_id = await db.get_link(unique_id)
    if not message_id:
        raise HTTPException(status_code=404, detail="Link expired or invalid.")
    main_bot = multi_clients.get(0)
    if not main_bot:
        raise HTTPException(status_code=503, detail="Bot is not ready.")
    try:
        message = await get_media_message(main_bot, message_id)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found on Telegram.")
    media = message.document or message.video or message.audio
    if not media:
        raise HTTPException(status_code=404, detail="Media not found in the message.")
    file_name = media.file_name or "file"
    mime_type = media.mime_type or "application/octet-stream"
    stream_link = f"{Config.BASE_URL}/dl/{message_id}/{quote(file_name, safe='')}"
    response_data = {
        "file_name": file_name,
        "file_size": get_readable_file_size(media.file_size),
        "is_media": mime_type.startswith(("video", "audio")),
        "mime_type": mime_type,
        "bot_username": Config.BOT_USERNAME,
        "stream_url": stream_link,
        "mx_player_link": f"intent:{stream_link}#Intent;action=android.intent.action.VIEW;type={mime_type};end",
        "vlc_player_link": f"intent:{stream_link}#Intent;action=android.intent.action.VIEW;type={mime_type};package=org.videolan.vlc;end"
    }
    return response_data

class ByteStreamer:
    def __init__(self,c:Client):self.client=c
    @staticmethod
    async def get_location(f:FileId): return raw.types.InputDocumentFileLocation(id=f.media_id,access_hash=f.access_hash,file_reference=f.file_reference,thumb_size=f.thumbnail_size)
    async def yield_file(self,f:FileId,i:int,o:int,fc:int,lc:int,pc:int,cs:int):
        c=self.client;work_loads[i]+=1;ms=c.media_sessions.get(f.dc_id)
        if ms is None:
            if f.dc_id!=await c.storage.dc_id():
                ak=await Auth(c,f.dc_id,await c.storage.test_mode()).create();ms=Session(c,f.dc_id,ak,await c.storage.test_mode(),is_media=True);await ms.start();ea=await c.invoke(raw.functions.auth.ExportAuthorization(dc_id=f.dc_id));await ms.invoke(raw.functions.auth.ImportAuthorization(id=ea.id,bytes=ea.bytes))
            else:ms=c.session
            c.media_sessions[f.dc_id]=ms
        loc=await self.get_location(f);cp=1
        try:
            while cp<=pc:
                r=await ms.invoke(raw.functions.upload.GetFile(location=loc,offset=o,limit=cs),retries=0)
                if isinstance(r,raw.types.upload.File):
                    chk=r.bytes
                    if not chk:break
                    if pc==1:yield chk[fc:lc]
                    elif cp==1:yield chk[fc:]
                    elif cp==pc:yield chk[:lc]
                    else:yield chk
                    cp+=1;o+=cs
                else:break
        finally:work_loads[i]-=1

@app.get("/dl/{mid}/{fname}")
async def stream_media(r:Request,mid:int,fname:str):
    if not work_loads: raise HTTPException(503)
    client_id = min(work_loads, key=work_loads.get)
    c = multi_clients.get(client_id)
    if not c: raise HTTPException(503)
    
    tc=class_cache.get(c) or ByteStreamer(c);class_cache[c]=tc
    try:
        msg=await get_media_message(c,mid);m=msg.document or msg.video or msg.audio
        if not m or msg.empty:raise FileNotFoundError
        fid=FileId.decode(m.file_id);fsize=m.file_size;rh=r.headers.get("Range","");fb,ub=0,fsize-1
        if rh:
            rps=rh.replace("bytes=","").split("-");fb=int(rps[0])
            if len(rps)>1 and rps[1]:ub=int(rps[1])
        if(ub>=fsize)or(fb<0):raise HTTPException(416)
        rl=ub-fb+1;cs=1024*1024;off=(fb//cs)*cs;fc=fb-off;lc=(ub%cs)+1
        # The first Telegram chunk starts at the aligned offset, so include
        # every source chunk touched by the requested byte range.
        pc=math.ceil((ub-off+1)/cs)
        body=tc.yield_file(fid,client_id,off,fc,lc,pc,cs);sc=206 if rh else 200
        hdrs={"Content-Type":m.mime_type or "application/octet-stream","Accept-Ranges":"bytes","Cache-Control":"private, no-store, max-age=0","Pragma":"no-cache","Content-Disposition":f'inline; filename="{m.file_name}"',"Content-Length":str(rl),"X-Content-Type-Options":"nosniff"}
        if rh:hdrs["Content-Range"]=f"bytes {fb}-{ub}/{fsize}"
        return StreamingResponse(body,status_code=sc,headers=hdrs)
    except FileNotFoundError:raise HTTPException(404)
    except Exception:print(traceback.format_exc());raise HTTPException(500)

# =====================================================================================
# --- MAIN EXECUTION BLOCK ---
# =====================================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    # Log level ko "info" rakho taaki hamara filter kaam kar sake
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
