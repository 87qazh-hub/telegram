import logging
import re
import yt_dlp
import os
import tempfile
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters # type: ignore
from http.cookiejar import MozillaCookieJar
import json

# Replace with your own Bot Token from BotFather
BOT_TOKEN = '8712417773:AAFcn5qJPya06SfM7Xiw9cFb3pRQvADCbIM'

# Instagram credentials (optional - for private content access)
# WARNING: Using credentials can violate Instagram's terms of service
# Only use with your own account and for personal use
INSTAGRAM_USERNAME = os.getenv('INSTAGRAM_USERNAME')  # Set this environment variable
INSTAGRAM_PASSWORD = os.getenv('INSTAGRAM_PASSWORD')  # Set this environment variable
COOKIE_FILE = os.getenv('INSTAGRAM_COOKIES', 'cookies.txt')  # Path to cookies file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
URL_REGEX = re.compile(r'https?://[^\s]+')


def extract_url(text: str) -> str:
    if not text:
        return ''
    match = URL_REGEX.search(text)
    if match:
        return match.group(0).rstrip('.,!')
    return text.strip()


def build_ydl_opts(tmp_dir: str) -> dict:
    opts = {
        'format': 'best',
        'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'restrictfilenames': True,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.instagram.com/',
        },
    }

    # Add cookie file if it exists
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE
        logger.info(f"Using cookies from {COOKIE_FILE}")

    # Add Instagram login if credentials are provided
    if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
        opts.update({
            'username': INSTAGRAM_USERNAME,
            'password': INSTAGRAM_PASSWORD,
        })
    return opts


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me an Instagram Reels link, and I'll try to download it!")


async def download_reel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = extract_url(update.message.text or "")

    if not url.startswith("http"):
        await update.message.reply_text("Please send a valid Instagram link.")
        return

    wait_msg = await update.message.reply_text("⏳ Downloading your reel, please wait...")
    filename = None

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ydl_opts = build_ydl_opts(tmp_dir)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if 'entries' in info:
                    info = info['entries'][0]

                filename = ydl.prepare_filename(info)
                title = info.get('title', 'reel')
                file_size = os.path.getsize(filename)

                if file_size > 45 * 1024 * 1024:
                    await wait_msg.edit_text("❌ The video is too large to send through Telegram (max 50MB).")
                    return

                with open(filename, 'rb') as video_file:
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
                    await update.message.reply_video(
                        video=video_file,
                        caption=f"🎬 {title}",
                        supports_streaming=True,
                    )

                await wait_msg.delete()

    except yt_dlp.utils.DownloadError as e:
        logger.exception("yt-dlp download error")
        error_msg = str(e).lower()
        try:
            if "private" in error_msg or "not available" in error_msg or "audience" in error_msg or "certain audiences" in error_msg:
                if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
                    await wait_msg.edit_text(
                        "❌ Could not access private reel even with login.\n\n"
                        "Possible reasons:\n"
                        "• The account blocked your login\n"
                        "• Two-factor authentication required\n"
                        "• Content is restricted to specific followers only\n"
                        "• Instagram rate limiting"
                    )
                else:
                    await wait_msg.edit_text(
                        "❌ This reel is private or restricted.\n\n"
                        "To download private content:\n"
                        "• Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD environment variables\n"
                        "• Or ask the owner to make their account public\n\n"
                        "⚠️ Note: Downloading private content may violate Instagram's terms of service."
                    )
            else:
                await wait_msg.edit_text(
                    f"❌ Download failed: {e}.\n\nThis can happen if the reel is deleted, restricted, or there's a network issue."
                )
        except Exception:
            pass

    except Exception as e:
        logger.exception("Unexpected error while downloading reel")
        try:
            await wait_msg.edit_text(
                "❌ Failed to download the reel. This could be because:\n"
                "• The account is private\n"
                "• The content is restricted\n"
                "• The URL is invalid\n\n"
                f"Error: {e}"
            )
        except Exception:
            pass

    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception:
                pass


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update", exc_info=context.error)
    try:
        await update.message.reply_text("❌ An unexpected error occurred. Please try again later.")
    except Exception:
        pass


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_reel))
    app.add_error_handler(error_handler)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
