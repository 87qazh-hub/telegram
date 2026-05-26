#!/usr/bin/env python3
"""
Telegram Reel Downloader Bot with import workarounds
"""

import os
import sys
import logging
import tempfile
import requests
import json
import time
from urllib.parse import urlparse, urlencode
from pathlib import Path

# Add current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Try to handle telegram imports with better error handling
try:
    from telegram import Update, InputFile
    from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
    TELEGRAM_AVAILABLE = True
    print("✓ Successfully imported telegram.ext")
except ImportError as e:
    print(f"⚠️ Telegram import warning: {e}")
    print("The bot will still work, but you might see import warnings in VS Code")
    # Create mock classes for type checking
    class Update:
        pass
    class ContextTypes:
        class DEFAULT_TYPE:
            pass
    TELEGRAM_AVAILABLE = False

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
    print("✓ Successfully imported yt-dlp")
except ImportError:
    print("❌ yt-dlp not available. Please install with: pip install yt-dlp")
    YT_DLP_AVAILABLE = False

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TelegramBotWrapper:
    """Wrapper class to handle Telegram API with or without telegram.ext"""
    
    def __init__(self, token):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.last_update_id = 0
    
    def make_request(self, method, params=None):
        """Make API request to Telegram"""
        url = self.base_url + method
        try:
            response = requests.post(url, data=params, timeout=30)
            return response.json()
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return None
    
    def get_updates(self):
        """Get new updates from Telegram"""
        params = {'timeout': 100, 'offset': self.last_update_id + 1}
        result = self.make_request('getUpdates', params)
        
        if result and result.get('ok'):
            return result['result']
        return []
    
    def send_message(self, chat_id, text):
        """Send text message"""
        params = {'chat_id': chat_id, 'text': text}
        return self.make_request('sendMessage', params)
    
    def send_video(self, chat_id, video_path, caption=""):
        """Send video file"""
        try:
            with open(video_path, 'rb') as video_file:
                files = {'video': video_file}
                params = {'chat_id': chat_id, 'caption': caption}
                response = requests.post(
                    self.base_url + 'sendVideo',
                    data=params,
                    files=files,
                    timeout=60
                )
                return response.json()
        except Exception as e:
            logger.error(f"Failed to send video: {e}")
            return None

class ReelDownloader:
    """Handle video downloading functionality"""
    
    def __init__(self):
        self.supported_domains = [
            'instagram.com', 'www.instagram.com',
            'youtube.com', 'www.youtube.com', 'youtu.be',
            'tiktok.com', 'vm.tiktok.com', 'www.tiktok.com',
            'facebook.com', 'www.facebook.com', 'fb.watch'
        ]
    
    def is_supported_url(self, url):
        """Check if URL is from supported platform"""
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            return any(supported in domain for supported in self.supported_domains)
        except:
            return False
    
    def download_video(self, url):
        """Download video using yt-dlp"""
        if not YT_DLP_AVAILABLE:
            raise Exception("yt-dlp is not available")
        
        ydl_opts = {
            'format': 'best[filesize<45M]',  # Limit to 45MB for Telegram
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
                    
                    # Handle playlists
                    if 'entries' in info:
                        info = info['entries'][0]
                    
                    return filename, info.get('title', 'video')
            except Exception as e:
                logger.error(f"Download error: {e}")
                raise

def main():
    """Main function"""
    # Get bot token
    BOT_TOKEN = os.getenv('BOT_TOKEN') or "7838259245:AAH9bQ2THx9017_IpNLVcCZ6Swyu1JDFd3k"
    
    if BOT_TOKEN == "HER7838259245:AAH9bQ2THx9017_IpNLVcCZ6Swyu1JDFd3kE":
        print("❌ Please set your BOT_TOKEN in environment variables or replace in code")
        return
    
    if not YT_DLP_AVAILABLE:
        print("❌ yt-dlp is required. Please install: pip install yt-dlp")
        return
    
    # Initialize components
    downloader = ReelDownloader()
    
    if TELEGRAM_AVAILABLE:
        print("🚀 Starting bot with telegram.ext...")
        run_with_telegram_ext(BOT_TOKEN, downloader)
    else:
        print("⚠️ Falling back to basic HTTP bot...")
        run_basic_bot(BOT_TOKEN, downloader)

def run_with_telegram_ext(token, downloader):
    """Run bot using telegram.ext library"""
    try:
        # Create application
        application = Application.builder().token(token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                             lambda update, context: handle_message(update, context, downloader)))
        
        # Error handler
        application.add_error_handler(error_handler)
        
        # Start bot
        print("🤖 Bot is starting with telegram.ext...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start with telegram.ext: {e}")
        print("Falling back to basic mode...")
        run_basic_bot(token, downloader)

def run_basic_bot(token, downloader):
    """Run bot using basic HTTP requests"""
    bot = TelegramBotWrapper(token)
    print("🤖 Basic bot is running... Press Ctrl+C to stop.")
    
    try:
        while True:
            updates = bot.get_updates()
            
            for update in updates:
                if 'update_id' in update:
                    bot.last_update_id = max(bot.last_update_id, update['update_id'])
                
                if 'message' in update:
                    message = update['message']
                    chat_id = message['chat']['id']
                    text = message.get('text', '').strip()
                    
                    if text == '/start':
                        bot.send_message(chat_id, get_welcome_text())
                    elif text == '/help':
                        bot.send_message(chat_id, get_help_text())
                    elif text and downloader.is_supported_url(text):
                        handle_url_basic(bot, chat_id, text, downloader)
                    elif text:
                        bot.send_message(chat_id, "❌ Please send a valid URL from Instagram, TikTok, YouTube, or Facebook")
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(get_welcome_text())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(get_help_text())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, downloader):
    """Handle incoming messages"""
    text = update.message.text.strip()
    
    if not downloader.is_supported_url(text):
        await update.message.reply_text("❌ Unsupported URL. Please send links from supported platforms.")
        return
    
    wait_msg = await update.message.reply_text("⏳ Downloading your video...")
    
    try:
        file_path, title = downloader.download_video(text)
        
        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > 45 * 1024 * 1024:
            await wait_msg.edit_text("❌ Video is too large (max 45MB).")
            return
        
        # Send video
        with open(file_path, 'rb') as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"🎬 {title}",
                supports_streaming=True
            )
        
        await wait_msg.delete()
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await wait_msg.edit_text("❌ Failed to download. Please try another video.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")

def handle_url_basic(bot, chat_id, url, downloader):
    """Handle URL in basic mode"""
    bot.send_message(chat_id, "⏳ Downloading your video...")
    
    try:
        file_path, title = downloader.download_video(url)
        
        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > 45 * 1024 * 1024:
            bot.send_message(chat_id, "❌ Video is too large (max 45MB).")
            return
        
        # Send video
        result = bot.send_video(chat_id, file_path, f"🎬 {title}")
        
        if result and result.get('ok'):
            bot.send_message(chat_id, "✅ Download complete!")
        else:
            bot.send_message(chat_id, "❌ Failed to send video.")
            
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.send_message(chat_id, "❌ Failed to download. Please try another video.")

def get_welcome_text():
    return """
👋 Hello! I'm a Video Downloader Bot.

I can download videos from:
• Instagram Reels
• TikTok Videos
• YouTube Shorts
• Facebook Reels

Just send me a link and I'll download it for you!

⚠️ Note: Instagram accounts must be public.
"""

def get_help_text():
    return """
📖 How to use:
1. Find a video you want to download
2. Copy the share link
3. Send the link to me
4. I'll download and send you the video!

🔗 Supported platforms:
- Instagram, TikTok, YouTube, Facebook

⚠️ Limitations:
- Max 45MB file size
- Public accounts only for Instagram
"""

if __name__ == "__main__":
    main()