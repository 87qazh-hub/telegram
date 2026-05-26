import os
import logging
import re
import tempfile
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import yt_dlp
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable
BOT_TOKEN = os.getenv('BOT_TOKEN') or '8712417773:AAFcn5qJPya06SfM7Xiw9cFb3pRQvADCbIM'

# Supported domains
SUPPORTED_DOMAINS = [
    'instagram.com', 'www.instagram.com',
    'youtube.com', 'www.youtube.com', 'youtu.be',
    'tiktok.com', 'vm.tiktok.com', 'www.tiktok.com',
    'facebook.com', 'www.facebook.com', 'fb.watch'
]

# Check if URL is supported
def is_supported_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        return any(supported_domain in domain for supported_domain in SUPPORTED_DOMAINS)
    except:
        return False

# Download media using yt-dlp
def download_reel(url):
    ydl_opts = {
        'format': 'best',
        'outtmpl': '%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        ydl_opts['outtmpl'] = os.path.join(tmp_dir, '%(title)s.%(ext)s')
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
                # Check if video has multiple formats
                if 'entries' in info:
                    info = info['entries'][0]
                
                return filename, info.get('title', 'reel')
        except Exception as e:
            logger.error(f"Error downloading reel: {e}")
            raise

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"""
👋 Hello {user.first_name}!

I'm a Reel Downloader Bot. I can download reels from:
• Instagram
• TikTok
• YouTube Shorts
• Facebook Reels

Just send me a link to any reel and I'll download it for you!

⚠️ Note: Please ensure the account is public for Instagram reels.
    """
    await update.message.reply_text(welcome_text)

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 How to use this bot:

1. Find a reel you want to download on Instagram, TikTok, YouTube, or Facebook
2. Copy the share link
3. Send the link to this bot
4. I'll download and send you the video!

🔗 Supported platforms:
- Instagram Reels
- TikTok Videos
- YouTube Shorts
- Facebook Reels

⚠️ Limitations:
- Instagram accounts must be public
- Some content might not be downloadable due to platform restrictions
    """
    await update.message.reply_text(help_text)

# Handle reel URLs
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    
    # Check if URL is supported
    if not is_supported_url(url):
        await update.message.reply_text("❌ This URL is not supported. Please send a link from Instagram, TikTok, YouTube, or Facebook.")
        return
    
    # Send waiting message
    wait_msg = await update.message.reply_text("⏳ Downloading your reel, please wait...")
    
    try:
        # Download the reel
        file_path, title = download_reel(url)
        
        # Check file size (Telegram has a 50MB limit for bots)
        file_size = os.path.getsize(file_path)
        if file_size > 45 * 1024 * 1024:  # 45MB to be safe
            await wait_msg.edit_text("❌ The video is too large to send through Telegram (max 50MB).")
            return
        
        # Send the video
        with open(file_path, 'rb') as video_file:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_video")
            await update.message.reply_video(
                video=video_file,
                caption=f"🎬 {title}",
                supports_streaming=True
            )
        
        # Delete waiting message
        await wait_msg.delete()
        
    except Exception as e:
        logger.error(f"Error processing reel: {e}")
        await wait_msg.edit_text("❌ Failed to download the reel. This could be because:\n• The account is private\n• The content is restricted\n• The URL is invalid\n\nPlease try another reel.")
        
        # Clean up temporary files
        try:
            if 'file_path' in locals() and os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")
    
    try:
        await update.message.reply_text("❌ An error occurred. Please try again later.")
    except:
        pass

def main():
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("🤖 Reel Downloader Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()