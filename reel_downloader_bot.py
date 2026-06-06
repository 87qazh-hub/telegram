import asyncio
import os
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)
import yt_dlp

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")

ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
DB_PATH = os.getenv('DB_PATH', 'downloads.db')

# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                username      TEXT,
                url           TEXT,
                title         TEXT,
                format        TEXT,
                file_size     INTEGER,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

def record_download(user_id, username, url, title, fmt, file_size):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO downloads (user_id,username,url,title,format,file_size) VALUES (?,?,?,?,?,?)',
            (user_id, username, url, title, fmt, file_size)
        )

def get_user_stats(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            'SELECT COUNT(*), COALESCE(SUM(file_size),0) FROM downloads WHERE user_id=?',
            (user_id,)
        ).fetchone()

def get_user_history(user_id, limit=5):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            'SELECT title,format,downloaded_at FROM downloads WHERE user_id=? ORDER BY downloaded_at DESC LIMIT ?',
            (user_id, limit)
        ).fetchall()

def get_admin_stats():
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute('SELECT COUNT(*) FROM downloads').fetchone()[0]
        users = conn.execute('SELECT COUNT(DISTINCT user_id) FROM downloads').fetchone()[0]
        top = conn.execute(
            'SELECT COALESCE(username,"unknown"), COUNT(*) c FROM downloads GROUP BY user_id ORDER BY c DESC LIMIT 5'
        ).fetchall()
    return total, users, top

# ── Supported platforms ───────────────────────────────────────────────────────

SUPPORTED_DOMAINS = {
    'instagram.com', 'www.instagram.com',
    'youtube.com', 'www.youtube.com', 'youtu.be',
    'tiktok.com', 'vm.tiktok.com', 'www.tiktok.com',
    'facebook.com', 'www.facebook.com', 'fb.watch',
    'pinterest.com', 'www.pinterest.com', 'pin.it',
    'twitter.com', 'www.twitter.com', 'x.com', 'www.x.com',
    'reddit.com', 'www.reddit.com', 'v.redd.it',
}

URL_RE = re.compile(r'https?://\S+')

def is_supported_url(url):
    try:
        return urlparse(url).netloc.lower() in SUPPORTED_DOMAINS
    except Exception:
        return False

def extract_urls(text):
    return [u for u in URL_RE.findall(text) if is_supported_url(u)]

# ── Main menu keyboard ────────────────────────────────────────────────────────

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📥 Download Video"), KeyboardButton("🎵 Download Audio")],
        [KeyboardButton("📊 My Stats"),        KeyboardButton("📜 My History")],
        [KeyboardButton("ℹ️ Help")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Paste a link or pick an option...",
)

MENU_ACTIONS = {"📥 Download Video", "🎵 Download Audio", "📊 My Stats", "📜 My History", "ℹ️ Help"}

# Short ID → URL cache (avoids Telegram's 64-byte callback_data limit)
_url_cache: dict[str, str] = {}

# ── Quality options ───────────────────────────────────────────────────────────

QUALITY = {
    'best':  ('Best',  'best[vcodec!=none][acodec!=none]/best'),
    '720p':  ('720p',  'best[height<=720][vcodec!=none][acodec!=none]/best[height<=720]'),
    '480p':  ('480p',  'best[height<=480][vcodec!=none][acodec!=none]/best[height<=480]'),
    '360p':  ('360p',  'best[height<=360][vcodec!=none][acodec!=none]/best[height<=360]'),
    'audio': ('Audio', 'bestaudio[ext=m4a]/bestaudio/best'),
}

def quality_keyboard(url):
    uid = str(uuid.uuid4())[:8]
    _url_cache[uid] = url
    buttons = [
        InlineKeyboardButton(label, callback_data=f"dl:{key}:{uid}")
        for key, (label, _) in QUALITY.items()
    ]
    return InlineKeyboardMarkup([buttons[:3], buttons[3:]])

# ── Download helpers ──────────────────────────────────────────────────────────

COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.txt')

def _base_opts():
    opts = {'quiet': True, 'no_warnings': True}
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    return opts

def fetch_info(url):
    opts = _base_opts()
    opts['skip_download'] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info['entries'][0] if 'entries' in info else info

def download_media(url, fmt, is_audio):
    tmp_dir = tempfile.mkdtemp()
    opts = _base_opts()
    opts.update({
        'outtmpl': os.path.join(tmp_dir, '%(title)s.%(ext)s'),
        'noplaylist': True,
    })
    if is_audio:
        opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
    else:
        opts['format'] = ('download_addr-0/' + fmt) if 'tiktok.com' in url else fmt
        opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if 'entries' in info:
                info = info['entries'][0]
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                files = os.listdir(tmp_dir)
                if files:
                    filename = os.path.join(tmp_dir, files[0])
            return filename, info.get('title', 'media'), tmp_dir, info.get('thumbnail')
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

async def send_media(user, reply_target, url, quality_key='best'):
    is_audio = quality_key == 'audio'
    fmt = QUALITY.get(quality_key, QUALITY['best'])[1]
    tmp_dir = None
    try:
        filepath, title, tmp_dir, _ = await asyncio.get_event_loop().run_in_executor(
            None, download_media, url, fmt, is_audio
        )
        file_size = os.path.getsize(filepath)
        if file_size > 45 * 1024 * 1024:
            await reply_target.reply_text("❌ File too large for Telegram (max 50MB).")
            return
        with open(filepath, 'rb') as f:
            if is_audio:
                await reply_target.reply_audio(audio=f, title=title, caption=f"🎵 {title}")
            else:
                await reply_target.reply_video(video=f, caption=f"🎬 {title}", supports_streaming=True)
        record_download(user.id, user.username, url, title, quality_key, file_size)
    except Exception as e:
        logger.error(f"send_media error: {e}")
        await reply_target.reply_text(
            "❌ Failed to download. The content may be private, restricted, or the URL is invalid."
        )
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Hello {name}! I can download videos from Instagram, TikTok, YouTube, Facebook, Twitter/X, Pinterest, and Reddit.\n\nPaste a link or use the menu below 👇",
        reply_markup=MAIN_MENU,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
📖 How to use:

1. Send any supported link
2. A preview + quality buttons appear
3. Tap a quality to download

🎵 Audio only: /audio <url>
📦 Batch: send multiple links in one message
🔍 Inline: type @thisbot <url> in any chat

Supported: Instagram, TikTok, YouTube, Facebook,
           Twitter/X, Pinterest, Reddit

⚠️ Instagram/Facebook require public accounts
⚠️ Max file size: 50MB (Telegram limit)
""")

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /audio <url>")
        return
    url = context.args[0]
    if not is_supported_url(url):
        await update.message.reply_text("❌ Unsupported URL.")
        return
    wait = await update.message.reply_text("⏳ Downloading audio...")
    await send_media(update.effective_user, update.message, url, 'audio')
    try:
        await wait.delete()
    except Exception:
        pass

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count, total_bytes = get_user_stats(update.effective_user.id)
    await update.message.reply_text(
        f"📊 Your Stats:\n• Downloads: {count}\n• Data used: {total_bytes / 1024 / 1024:.1f} MB"
    )

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_user_history(update.effective_user.id)
    if not rows:
        await update.message.reply_text("No download history yet.")
        return
    lines = ["📜 Last 5 downloads:\n"]
    for title, fmt, ts in rows:
        lines.append(f"• {title[:40]} [{fmt}] — {ts[:10]}")
    await update.message.reply_text('\n'.join(lines))

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    total, users, top = get_admin_stats()
    lines = [f"🔧 Admin Panel\n• Total downloads: {total}\n• Unique users: {users}\n\n👥 Top users:"]
    for username, cnt in top:
        lines.append(f"  @{username}: {cnt} downloads")
    await update.message.reply_text('\n'.join(lines))

# ── Message handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ── Menu button taps ──
    if text == "ℹ️ Help":
        await help_command(update, context)
        return
    if text == "📊 My Stats":
        await stats_command(update, context)
        return
    if text == "📜 My History":
        await history_command(update, context)
        return
    if text == "📥 Download Video":
        await update.message.reply_text("🔗 Send me the video link now:")
        context.user_data['mode'] = 'video'
        return
    if text == "🎵 Download Audio":
        await update.message.reply_text("🔗 Send me the link and I'll extract the audio:")
        context.user_data['mode'] = 'audio'
        return

    # ── Link handling ──
    urls = extract_urls(text)
    if not urls:
        await update.message.reply_text(
            "❌ No supported URL found.\n\nPaste a link or use the menu below 👇",
            reply_markup=MAIN_MENU,
        )
        return

    # If user tapped "Download Audio" first, honour that mode
    if context.user_data.pop('mode', None) == 'audio' and len(urls) == 1:
        wait = await update.message.reply_text("⏳ Downloading audio...")
        await send_media(update.effective_user, update.message, urls[0], 'audio')
        try:
            await wait.delete()
        except Exception:
            pass
        return

    if len(urls) > 1:
        await update.message.reply_text(f"📦 Found {len(urls)} links — downloading all in best quality...")
        for i, url in enumerate(urls, 1):
            status = await update.message.reply_text(f"⏳ [{i}/{len(urls)}] Downloading...")
            await send_media(update.effective_user, update.message, url, 'best')
            try:
                await status.delete()
            except Exception:
                pass
        return

    url = urls[0]
    wait = await update.message.reply_text("🔍 Fetching info...")
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, fetch_info, url)
        title = info.get('title', 'Video')[:60]
        thumbnail = info.get('thumbnail')
        caption = f"🎬 *{title}*\n\nChoose quality:"
        await wait.delete()
        if thumbnail:
            try:
                await update.message.reply_photo(
                    photo=thumbnail,
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=quality_keyboard(url)
                )
                return
            except Exception:
                pass
        await update.message.reply_text(caption, parse_mode='Markdown', reply_markup=quality_keyboard(url))
    except Exception as e:
        logger.error(f"fetch_info error: {e}")
        try:
            await wait.delete()
        except Exception:
            pass
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))

# ── Callback handler ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, quality_key, uid = query.data.split(':', 2)
    url = _url_cache.get(uid)
    if not url:
        await query.message.reply_text("❌ Session expired. Please send the link again.")
        return
    label = QUALITY[quality_key][0]
    try:
        if query.message.caption is not None:
            await query.edit_message_caption(caption=f"⏳ Downloading ({label})...")
        else:
            await query.edit_message_text(f"⏳ Downloading ({label})...")
    except Exception:
        pass
    await send_media(query.from_user, query.message, url, quality_key)

# ── Inline handler ────────────────────────────────────────────────────────────

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.strip()
    if not query_text or not is_supported_url(query_text):
        return
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, fetch_info, query_text)
        title = info.get('title', 'Video')[:50]
        thumbnail = info.get('thumbnail')
        kwargs = {'thumbnail_url': thumbnail} if thumbnail else {}
        result = InlineQueryResultArticle(
            id='1',
            title=f"📥 {title}",
            description="Tap to send this link to the bot for download",
            input_message_content=InputTextMessageContent(query_text),
            **kwargs,
        )
        await update.inline_query.answer([result], cache_time=30)
    except Exception as e:
        logger.error(f"Inline error: {e}")

# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update error: {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ An error occurred. Please try again.")
    except Exception:
        pass

# ── Health check server ───────────────────────────────────────────────────────

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args):
        pass

# ── Entry point ───────────────────────────────────────────────────────────────

async def run_bot():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("audio", audio_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r'^dl:'))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    async with app:
        await app.bot.set_my_commands([
            BotCommand("start",   "Open main menu"),
            BotCommand("help",    "How to use the bot"),
            BotCommand("audio",   "Download audio: /audio <url>"),
            BotCommand("stats",   "Your download stats"),
            BotCommand("history", "Last 5 downloads"),
            BotCommand("admin",   "Admin panel"),
        ])
        await app.start()
        await app.updater.start_polling()
        print("🤖 Bot running with all features!")
        await asyncio.Event().wait()

def main():
    port = int(os.getenv('PORT', 0))
    if port:
        threading.Thread(
            target=HTTPServer(('0.0.0.0', port), _Health).serve_forever,
            daemon=True
        ).start()
        print(f"Health server on port {port}")
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
