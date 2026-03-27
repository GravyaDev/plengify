"""Telegram bot — bridge between Telegram and the Agent container.

- File upload/download support (tar.gz, documents, images)
- Markdown → Telegram HTML conversion
- Typing indicator while agent thinks
- Graceful timeout handling
"""
import asyncio
import html
import json
import logging
import os
import re
import sys
import threading
import time

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("telegram-bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = set(os.environ.get("TELEGRAM_CHAT_ID", "").split(","))
AGENT_URL = os.environ.get("AGENT_URL", "http://agent:8000")
PLATFORM_URL = os.environ.get("PLATFORM_API_URL", "http://platform-api:8000")
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/opt/pleng/projects")


def _is_allowed(update: Update) -> bool:
    chat_id = str(update.effective_chat.id)
    if chat_id not in ALLOWED_CHAT_IDS:
        logger.warning(f"Blocked: {chat_id}")
        return False
    return True


# ── Markdown → Telegram HTML ────────────────────────────

def md_to_tg(text: str) -> str:
    """Convert LLM markdown to Telegram HTML.
    Pipeline: extract protected blocks → escape HTML → apply formatting → restore blocks.
    Follows OpenClaw's approach: always HTML parse_mode, tag-aware chunking, plain text fallback."""

    blocks: dict[str, str] = {}
    counter = [0]

    def save_block(content: str, tag: str = "pre") -> str:
        key = f"\x00BLOCK{counter[0]}\x00"
        counter[0] += 1
        blocks[key] = f"<{tag}>{html.escape(content)}</{tag}>"
        return key

    # 1. Extract fenced code blocks (``` with optional language, tolerant of spacing)
    text = re.sub(
        r'```[ \t]*\w*[ \t]*\n(.*?)```',
        lambda m: save_block(m.group(1)),
        text, flags=re.DOTALL,
    )

    # 2. Extract markdown tables → pre-formatted
    text = re.sub(r'(?:^\|.+\|$\n?)+', lambda m: save_block(m.group(0)), text, flags=re.MULTILINE)

    # 3. Escape HTML in remaining text
    text = html.escape(text)

    # 4. Inline code (before bold/italic to avoid conflicts)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)

    # 5. Bold: **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)

    # 6. Italic: *text* (not preceded/followed by *)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', text)

    # 7. Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 8. Headers → bold
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 9. Blockquotes: > text → <blockquote>
    text = re.sub(
        r'(^&gt; .+(?:\n&gt; .+)*)',
        lambda m: '<blockquote>' + re.sub(r'^&gt; ', '', m.group(0), flags=re.MULTILINE) + '</blockquote>',
        text, flags=re.MULTILINE,
    )

    # 10. Bullet lists
    text = re.sub(r'^[-•]\s+', '• ', text, flags=re.MULTILINE)

    # 11. Numbered lists: clean up "1. " → "1. " (preserve but normalize)
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)

    # 12. Links: [text](url) → <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 13. Wrap bare file references in <code> to prevent Telegram auto-linking
    # (e.g. README.md → <code>README.md</code>, but skip if already inside a tag)
    text = re.sub(
        r'(?<![<\w/])(\b[\w./-]+\.(?:md|ts|tsx|js|jsx|py|rs|go|yaml|yml|toml|json|sh|css|html|sql|env|lock|cfg|txt|csv|xml))\b(?![^<]*>)',
        r'<code>\1</code>',
        text,
    )

    # 14. Restore saved blocks
    for key, block_html in blocks.items():
        text = text.replace(html.escape(key), block_html)
        text = text.replace(key, block_html)

    return text


def _split_html_chunks(text: str, max_len: int = 4000) -> list[str]:
    """Split Telegram HTML into chunks, re-opening/closing tags at boundaries.
    Ensures each chunk is valid HTML that Telegram can parse."""
    if len(text) <= max_len:
        return [text]

    # Tags that Telegram supports
    TAG_RE = re.compile(r'<(/?)(\w[\w-]*)(?:\s[^>]*)?>|([^<]+|<)', re.DOTALL)
    VOID_TAGS = {'br', 'hr', 'img'}

    chunks: list[str] = []
    open_tags: list[str] = []  # stack of currently open tag names
    current = ''

    for m in TAG_RE.finditer(text):
        token = m.group(0)
        is_close = m.group(1) == '/'
        tag_name = m.group(2)

        # Check if adding this token would exceed the limit
        # Account for closing tags we'd need to add
        close_overhead = sum(len(f'</{t}>') for t in reversed(open_tags))
        if len(current) + len(token) + close_overhead > max_len and current.strip():
            # Close open tags in this chunk
            for t in reversed(open_tags):
                current += f'</{t}>'
            chunks.append(current)
            # Re-open tags in the next chunk
            current = ''
            for t in open_tags:
                current += f'<{t}>'

        current += token

        # Track open/close tags
        if tag_name and tag_name not in VOID_TAGS:
            if is_close:
                if open_tags and open_tags[-1] == tag_name:
                    open_tags.pop()
            else:
                open_tags.append(tag_name)

    # Flush remaining
    if current.strip():
        for t in reversed(open_tags):
            current += f'</{t}>'
        chunks.append(current)

    return chunks or [text]


# ── Commands ────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text(
        "<b>Pleng</b> — Your AI Platform Engineer\n\n"
        "Tell me what you need:\n"
        "• <i>deploy github.com/user/repo</i>\n"
        "• <i>build me a booking API with Postgres</i>\n"
        "• Send me a tar.gz or any file\n\n"
        "/sites — list sites\n"
        "/new — new conversation\n"
        "/help — help",
        parse_mode="HTML",
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    chat_id = str(update.effective_chat.id)
    try:
        requests.post(f"{AGENT_URL}/chat/reset", json={"session_id": chat_id}, timeout=5)
    except Exception:
        pass
    await update.message.reply_text("Session reset.")


async def cmd_sites(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    try:
        sites = requests.get(f"{PLATFORM_URL}/api/sites", timeout=10).json()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return

    if not sites:
        await update.message.reply_text("No sites deployed yet.")
        return

    icons = {"staging": "🟡", "production": "🟢", "stopped": "🔴", "generating": "🟣", "error": "❌"}
    text = "<b>Sites:</b>\n\n"
    for s in sites:
        icon = icons.get(s["status"], "⚪")
        domain = s.get("production_domain") or s.get("staging_domain") or ""
        url = f"https://{domain}" if s.get("production_domain") else f"http://{domain}" if domain else ""
        text += f"{icon} <b>{s['name']}</b> — {s['status']}\n"
        if url:
            text += f"    {url}\n"
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "<b>Pleng — Your AI Platform Engineer</b>\n\n"
        "<b>Commands:</b>\n"
        "/new — new conversation\n"
        "/sites — list sites\n"
        "/help — help\n\n"
        "<b>You can say:</b>\n"
        "• deploy github.com/user/repo\n"
        "• build me an app that does X\n"
        "• logs / stop / restart my-app\n"
        "• promote my-app to app.example.com\n\n"
        "<b>Files:</b>\n"
        "• Send tar.gz, zip, or any file\n"
        "• Ask me to send you a project as tar.gz",
        parse_mode="HTML",
    )


# ── Message handler (text + files) ──────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    chat_id = str(update.effective_chat.id)
    message = update.message
    user_text = message.text or message.caption or ""

    # Handle file attachments
    attachments = []
    if message.document:
        doc = message.document
        file = await doc.get_file()
        save_dir = os.path.join(PROJECTS_DIR, "_uploads")
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, doc.file_name or f"file_{doc.file_id}")
        await file.download_to_drive(file_path)
        attachments.append({"name": doc.file_name, "path": file_path, "size": doc.file_size})
        if not user_text:
            user_text = f"I'm sending you the file: {doc.file_name}"

    if message.photo:
        photo = message.photo[-1]
        file = await photo.get_file()
        save_dir = os.path.join(PROJECTS_DIR, "_uploads")
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, f"photo_{photo.file_id}.jpg")
        await file.download_to_drive(file_path)
        attachments.append({"name": "photo.jpg", "path": file_path})
        if not user_text:
            user_text = "I'm sending you a photo"

    if not user_text:
        return

    # Add attachment info to message
    if attachments:
        user_text += "\n\nAttached files:\n"
        for a in attachments:
            user_text += f"- {a['name']} (saved at {a['path']})\n"
        user_text += "You can read these files with the Read tool."

    await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")

    threading.Thread(
        target=_agent_respond,
        args=(chat_id, user_text),
        daemon=True,
    ).start()


def _agent_respond(chat_id: str, message: str):
    """Call agent, keep typing, send response + any files."""
    typing = True

    def keep_typing():
        while typing:
            try:
                asyncio.run_coroutine_threadsafe(
                    _app.bot.send_chat_action(chat_id=chat_id, action="typing"),
                    _loop,
                ).result(timeout=5)
            except Exception:
                pass
            time.sleep(4)

    t = threading.Thread(target=keep_typing, daemon=True)
    t.start()

    try:
        r = requests.post(
            f"{AGENT_URL}/chat",
            json={"message": message, "session_id": chat_id},
            timeout=7200,  # 2 hours max
        )
        data = r.json()
        response = data.get("response", "No response.")
    except requests.exceptions.ReadTimeout:
        response = "The agent is still working but it's taking too long for one message. Try asking for the status or a simpler task."
    except requests.ConnectionError:
        response = "Agent not available. Try again in a moment."
    except Exception as e:
        response = f"Error: {e}"

    typing = False

    # Send text response
    _send_text(chat_id, response)

    # Check if response mentions sending a file
    _check_and_send_files(chat_id, response)


def _check_and_send_files(chat_id: str, response: str):
    """If the agent mentions a file path, offer to send it."""
    # Look for tar.gz or zip paths in the response
    file_patterns = re.findall(r'(/opt/pleng/projects/[^\s\n]+\.(?:tar\.gz|zip|tgz))', response)
    for path in file_patterns:
        if os.path.exists(path):
            _send_file(chat_id, path)


def _send_text(chat_id: str, text: str):
    if not _app or not _loop:
        return

    formatted = md_to_tg(text)
    chunks = _split_html_chunks(formatted)

    for chunk in chunks:
        async def _do_send(c=chunk):
            try:
                await _app.bot.send_message(chat_id=chat_id, text=c, parse_mode="HTML")
            except Exception:
                try:
                    plain = re.sub(r'<[^>]+>', '', c)
                    await _app.bot.send_message(chat_id=chat_id, text=plain)
                except Exception as e:
                    logger.error(f"Send failed: {e}")

        asyncio.run_coroutine_threadsafe(_do_send(), _loop)


def _send_file(chat_id: str, file_path: str):
    """Send a file as a Telegram document."""
    if not _app or not _loop:
        return

    async def _do_send():
        try:
            with open(file_path, "rb") as f:
                await _app.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=os.path.basename(file_path),
                )
        except Exception as e:
            logger.error(f"File send failed: {e}")

    asyncio.run_coroutine_threadsafe(_do_send(), _loop)


_app = None
_loop = None


def main():
    global _app, _loop

    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    # Create uploads dir
    os.makedirs(os.path.join(PROJECTS_DIR, "_uploads"), exist_ok=True)

    logger.info("Starting Telegram bot...")

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    _app = Application.builder().token(TOKEN).build()

    _app.add_handler(CommandHandler("start", cmd_start))
    _app.add_handler(CommandHandler("new", cmd_new))
    _app.add_handler(CommandHandler("sites", cmd_sites))
    _app.add_handler(CommandHandler("help", cmd_help))
    _app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL | filters.PHOTO, handle_message))

    async def run():
        await _app.initialize()
        await _app.bot.delete_webhook(drop_pending_updates=True)
        await _app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        await _app.start()
        logger.info("Telegram bot polling started")
        while True:
            await asyncio.sleep(3600)

    _loop.run_until_complete(run())


if __name__ == "__main__":
    main()
