"""
mybrowse — Multi-Agent Browser AI

Mode:
  python run.py            → CLI mode (satu task langsung)
  python run.py --telegram → Telegram bot

Arsitektur:
  Channel (Telegram/CLI/...)
    → Supervisor (LLM orchestrator)
        → BrowserAgent  : browsing web via browser-use
        → ChatAgent     : Q&A, reasoning, penulisan
        → MemoryAgent   : simpan & recall memory

Environment (.env):
  OPENAI_API_KEY         → API key LLM
  OPENAI_BASE_URL        → Base URL (default: https://api.openai.com/v1)
  OPENAI_MODEL           → Model name (default: gpt-4o)
  TELEGRAM_BOT_TOKEN     → Token dari @BotFather
  TELEGRAM_ALLOWED_USERS → Chat ID diizinkan, koma (kosong = semua)
  CHROME_PATH            → Path Chrome executable
  AGENT_HEADLESS         → true/false (default: false)
  AGENT_MAX_STEPS        → Maks langkah browser agent (default: 50)
  DATABASE_URL           → PostgreSQL URL
"""

import asyncio
import logging
import os
import signal
import sys

os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('mybrowse')

from browser_use.llm import ChatOpenAI

import db
from agents.base import AgentContext, BrowserConfig
from agents.supervisor import Supervisor

# ─── Config ──────────────────────────────────────────────────────────────────

CHROME_PATH = os.getenv('CHROME_PATH', 'C:/Program Files/Google/Chrome/Application/chrome.exe')
HEADLESS = os.getenv('AGENT_HEADLESS', 'false').lower() == 'true'
MAX_STEPS = int(os.getenv('AGENT_MAX_STEPS', '50'))

LLM = ChatOpenAI(
    model=os.getenv('OPENAI_MODEL', 'gpt-4o'),
    base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
    api_key=os.getenv('OPENAI_API_KEY', ''),
)

BROWSER_CONFIG = BrowserConfig(
    headless=HEADLESS,
    executable_path=CHROME_PATH,
    max_steps=MAX_STEPS,
)

# ─── CLI Mode ─────────────────────────────────────────────────────────────────


async def run_cli(task: str) -> None:
    """Jalankan satu task dari command line."""
    try:
        await db.get_pool()
    except Exception as e:
        logger.warning(f'DB tidak tersedia: {e}')

    supervisor = Supervisor(llm=LLM, config=BROWSER_CONFIG)

    print(f'\nTask: {task}')
    print('─' * 60)

    async def on_update(status: str) -> None:
        print(f'  → {status}')

    ctx = AgentContext(
        task=task,
        channel='cli',
        channel_id='local',
        username='cli_user',
        on_update=on_update,
    )

    result = await supervisor.run(ctx)

    print('─' * 60)
    print(f'Agent: {result.agent_used}')
    print(f'Status: {"✓ Selesai" if result.success else "✗ Gagal"}')
    print(f'Langkah: {result.steps}')
    print()
    print(result.output)

    if result.attachments:
        print(f'\nAttachments: {result.attachments}')
    if result.errors:
        print(f'\nErrors: {result.errors}')

    await db.close_pool()


# ─── Telegram Mode ────────────────────────────────────────────────────────────


async def run_telegram() -> None:
    """Jalankan Telegram bot."""
    from channels.telegram import TelegramChannel

    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token:
        print('ERROR: TELEGRAM_BOT_TOKEN belum diset di .env')
        sys.exit(1)

    # Init DB
    try:
        await db.get_pool()
        logger.info('Database pool siap.')
    except Exception as e:
        logger.warning(f'Database tidak tersedia: {e}. Lanjut tanpa DB.')

    allowed_raw = os.getenv('TELEGRAM_ALLOWED_USERS', '').strip()
    allowed_users = [int(u.strip()) for u in allowed_raw.split(',') if u.strip().isdigit()]

    supervisor = Supervisor(llm=LLM, config=BROWSER_CONFIG)
    bot = TelegramChannel(
        supervisor=supervisor,
        token=token,
        allowed_users=allowed_users if allowed_users else None,
    )

    loop = asyncio.get_running_loop()

    async def _shutdown() -> None:
        logger.info('Shutdown...')
        await bot.stop()
        await db.close_pool()

    def _signal_handler() -> None:
        asyncio.create_task(_shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, OSError):
            pass

    logger.info('Telegram bot berjalan. Ctrl+C untuk berhenti.')
    try:
        await bot.start()
    finally:
        await db.close_pool()


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]

    if '--telegram' in args:
        asyncio.run(run_telegram())
    else:
        task = ' '.join(a for a in args if not a.startswith('--')) or 'Go to google.com and return the page title'
        asyncio.run(run_cli(task))
