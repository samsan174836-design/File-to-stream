# app.py (THE REAL, FINAL, CLEAN, EASY-TO-READ FULL CODE)

import os
import asyncio
import secrets
import time
import traceback
import uvicorn
import re
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from pyrogram.errors import FloodWait, UserNotParticipant
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pyrogram.file_id import FileId
from pyrogram import raw
from pyrogram.session import Session, Auth
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
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
    
    global bot_ready, startup_error
    bot_ready = False
    startup_error = None

    try:
        print("Starting main Pyrogram bot...")
        await bot.start()
        
        me = await bot.get_me()
        Config.BOT_USERNAME = me.username
        print(f"✅ Main Bot [@{Config.BOT_USERNAME}] safaltapoorvak start ho gaya.")

        # --- MULTI-CLIENT STARTUP ---
        multi_clients[0] = bot
        work_loads[0] = 0
        await initialize_clients()
        
        print(f"Verifying storage channel ({Config.STORAGE_CHANNEL})...")
        await bot.get_chat(Config.STORAGE_CHANNEL)
        print("✅ Storage channel accessible hai.")

        if Config.FORCE_SUB_CHANNEL:
            try:
                print(f"Verifying force sub channel ({Config.FORCE_SUB_CHANNEL})...")
                await bot.get_chat(Config.FORCE_SUB_CHANNEL)
                print("✅ Force Sub channel accessible hai.")
            except Exception as e:
                print(f"!!! WARNING: Bot, Force Sub channel mein admin nahi hai. Error: {e}")
        
        try:
            await cleanup_channel(bot)
        except Exception as e:
            print(f"Warning: Channel cleanup fail ho gaya. Error: {e}")

        print("--- Lifespan: Startup safaltapoorvak poora hua. ---")
        bot_ready = True
    except FloodWait as e:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=e.value)
        startup_error = (
            f"Telegram rate limit active. Do not restart the dyno until "
            f"{retry_at.isoformat()} (wait {e.value} seconds)."
        )
        multi_clients.clear()
        work_loads.clear()
        print(f"!!! BOT STARTUP BLOCKED: {startup_error}")
    except ValueError as e:
        if "Peer id invalid" in str(e):
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
    except Exception as e:
        startup_error = "Bot startup failed. Check the Heroku logs for the Telegram error."
        multi_clients.clear()
        work_loads.clear()
        print(f"!!! BOT STARTUP FAILED: {traceback.format_exc()}")
    
    yield
    
    print("--- Lifespan: Server band ho raha hai... ---")
    if bot.is_initialized:
        try:
            await bot.stop()
        except Exception as e:
            print(f"Warning: Bot shutdown failed: {e}")
    await db.disconnect()
    print("--- Lifespan: Shutdown poora hua. ---")

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
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
multi_clients = {}; work_loads = {}; class_cache = {}
bot_ready = False
startup_error = None
recent_uploads = {}

# =====================================================================================
# --- MULTI-CLIENT LOGIC ---
# =====================================================================================

class TokenParser:
    """ Environment variables se MULTI_TOKENs ko parse karta hai. """
    @staticmethod
    def parse_from_env():
        return {
            c + 1: t
            for c, (_, t) in enumerate(
                filter(lambda n: n[0].startswith("MULTI_TOKEN"), sorted(os.environ.items()))
            )
        }

async def start_client(client_id, bot_token):
    """ Ek naye client bot ko start karta hai. """
    try:
        print(f"Attempting to start Client: {client_id}")
        client = await Client(
            name=str(client_id), 
            api_id=Config.API_ID, 
            api_hash=Config.API_HASH,
            bot_token=bot_token, 
            no_updates=True, 
            in_memory=True
        ).start()
        work_loads[client_id] = 0
        multi_clients[client_id] = client
        print(f"✅ Client {client_id} started successfully.")
    except Exception as e:
        print(f"!!! CRITICAL ERROR: Failed to start Client {client_id} - Error: {e}")

async def initialize_clients():
    """ Saare additional clients ko initialize karta hai. """
    all_tokens = TokenParser.parse_from_env()
    if not all_tokens:
        print("No additional clients found. Using default bot only.")
        return
    
    print(f"Found {len(all_tokens)} extra clients. Starting them...")
    tasks = [start_client(i, token) for i, token in all_tokens.items()]
    await asyncio.gather(*tasks)

    if len(multi_clients) > 1:
        print(f"✅ Multi-Client Mode Enabled. Total Clients: {len(multi_clients)}")

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
                retry_button = InlineKeyboardButton("✅ Joined", url=f"https://t.me/{Config.BOT_USERNAME}?start={message.command[1]}")
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
        reply_text = f"""
👋 **Hello, {user_name}!**

__Welcome To Sharing Box Bot. I Can Help You Create Permanent, Shareable Links For Your Files.__

**How To Use Me:**

__Just Send Or Forward Any File To Me And I will instantly give you a special link that you can share with anyone!__
"""
        await message.reply_text(reply_text)

async def handle_file_upload(message: Message, user_id: int):
    unique_id = secrets.token_urlsafe(8)
    source_chat_id = message.chat.id if message.chat else user_id
    source_message_id = message.id
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

        sent_message = await message.copy(chat_id=Config.STORAGE_CHANNEL)
        await db.complete_link(unique_id, sent_message.id)

        media = message.document or message.video or message.audio
        original_name = media.file_name or "file"
        safe_name = "".join(
            character
            for character in original_name
            if character.isalnum() or character in (" ", ".", "_", "-")
        ).strip() or "file"
        encoded_name = quote(safe_name, safe="")
        download_link = f"{Config.BASE_URL}/dl/{sent_message.id}/{encoded_name}"
        stream_link = f"{Config.BASE_URL}/show/{unique_id}"
        display_name = escape(original_name)
        escaped_download_link = escape(download_link, quote=True)
        escaped_stream_link = escape(stream_link, quote=True)

        reply_text = (
            "✅ <b>Your Links Are Ready!</b>\n\n"
            f"📁 <b>File:</b> {display_name}\n\n"
            f"📊 <b>Size:</b> {get_readable_file_size(media.file_size)}\n\n"
            f"📥 <b>Download Link:</b> "
            f'<a href="{escaped_download_link}">{escaped_download_link}</a>\n\n'
            f"🎬 <b>Stream Link:</b> "
            f'<a href="{escaped_stream_link}">{escaped_stream_link}</a>\n\n'
            "⏳ <i>This link is permanent while the file remains available.</i>"
        )
        button = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🎬 Stream", url=stream_link),
                InlineKeyboardButton("📥 Download", url=download_link),
            ]]
        )

        reply_attempted = True
        await message.reply_text(
            reply_text,
            reply_markup=button,
            quote=True,
            disable_web_page_preview=True,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await db.release_link(unique_id)
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
async def file_handler(_, message: Message):
    await handle_file_upload(message, message.from_user.id)

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

# =====================================================================================
# --- FASTAPI WEB SERVER ---
# =====================================================================================
 
@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Render the public landing page without changing individual video links."""
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"request": request, "bot_ready": bot_ready},
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

@app.get("/api/catalog", response_class=JSONResponse)
async def get_catalog():
    """Return the frontend-friendly catalog without changing individual file links."""
    if not bot_ready:
        return {"status": "starting", "videos": []}

    main_bot = multi_clients.get(0)
    if not main_bot:
        return {"status": "starting", "videos": []}

    videos = []
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    for link in await db.list_ready_links(limit=100):
        unique_id = str(link.get("_id", ""))
        message_id = link.get("message_id")
        if not unique_id or not message_id:
            continue
        try:
            message = await main_bot.get_messages(Config.STORAGE_CHANNEL, message_id)
            media = message.document or message.video or message.audio
            if not media:
                continue
            file_name = media.file_name or "Untitled video"
            mime_type = media.mime_type or "application/octet-stream"
            created_at = message.date.isoformat() if message.date else None
            message_day = message.date.astimezone(ZoneInfo("Asia/Kolkata")).date() if message.date else None
            duration = getattr(media, "duration", None)
            videos.append({
                "id": unique_id,
                "title": _catalog_title(file_name),
                "fileName": file_name,
                "thumbnail": None,
                "videoUrl": f"/show/{quote(unique_id)}",
                "duration": duration,
                "category": _catalog_category(file_name, mime_type),
                "subject": None,
                "lectureNumber": None,
                "createdAt": created_at,
                "isToday": message_day == today,
                "mimeType": mime_type,
                "fileSize": media.file_size,
            })
        except Exception:
            logging.exception("Catalog item could not be loaded for link %s", unique_id)

    return {"status": "ok", "videos": videos}

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
        message = await main_bot.get_messages(Config.STORAGE_CHANNEL, message_id)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found on Telegram.")
    media = message.document or message.video or message.audio
    if not media:
        raise HTTPException(status_code=404, detail="Media not found in the message.")
    file_name = media.file_name or "file"
    safe_file_name = "".join(c for c in file_name if c.isalnum() or c in (' ', '.', '_', '-')).rstrip()
    mime_type = media.mime_type or "application/octet-stream"
    response_data = {
        "file_name": file_name,
        "file_size": get_readable_file_size(media.file_size),
        "is_media": mime_type.startswith(("video", "audio")),
        "mime_type": mime_type,
        "bot_username": Config.BOT_USERNAME,
        "direct_dl_link": f"{Config.BASE_URL}/dl/{message_id}/{safe_file_name}",
        "mx_player_link": f"intent:{Config.BASE_URL}/dl/{message_id}/{safe_file_name}#Intent;action=android.intent.action.VIEW;type={mime_type};end",
        "vlc_player_link": f"intent:{Config.BASE_URL}/dl/{message_id}/{safe_file_name}#Intent;action=android.intent.action.VIEW;type={mime_type};package=org.videolan.vlc;end"
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
        msg=await c.get_messages(Config.STORAGE_CHANNEL,mid);m=msg.document or msg.video or msg.audio
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
        hdrs={"Content-Type":m.mime_type or "application/octet-stream","Accept-Ranges":"bytes","Content-Disposition":f'inline; filename="{m.file_name}"',"Content-Length":str(rl)}
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
