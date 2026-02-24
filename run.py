"""
mybrowse - Browser Agent Runner

Mode:
  python run.py            → jalankan task langsung (CLI mode)
  python run.py --telegram → jalankan bot Telegram (terima perintah dari Telegram)

Environment (.env):
  OPENAI_API_KEY          → API key untuk LLM
  OPENAI_BASE_URL         → Base URL LLM (default: https://api.openai.com/v1)
  TELEGRAM_BOT_TOKEN      → Token bot dari @BotFather
  TELEGRAM_ALLOWED_USERS  → Chat ID yang diizinkan, pisah koma (kosong = semua)
  CHROME_PATH             → Path ke Chrome (default: deteksi otomatis)
  AGENT_HEADLESS          → true/false (default: false)
  AGENT_MAX_STEPS         → Maksimum langkah agent (default: 50)
"""

import asyncio
import os
import signal
import sys

os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

from dotenv import load_dotenv

load_dotenv()

from browser_use.llm import ChatOpenAI

from channels.base import AgentConfig, AgentRunner

# ─── Konfigurasi dari .env ───────────────────────────────────────────────────

CHROME_PATH = os.getenv('CHROME_PATH', 'C:/Program Files/Google/Chrome/Application/chrome.exe')
HEADLESS = os.getenv('AGENT_HEADLESS', 'false').lower() == 'true'
MAX_STEPS = int(os.getenv('AGENT_MAX_STEPS', '50'))

LLM = ChatOpenAI(
	model=os.getenv('OPENAI_MODEL', 'gpt-4o'),
	base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
	api_key=os.getenv('OPENAI_API_KEY', ''),
)

AGENT_CONFIG = AgentConfig(
	max_steps=MAX_STEPS,
	headless=HEADLESS,
	executable_path=CHROME_PATH,
)

# ─── Mode: CLI langsung ───────────────────────────────────────────────────────


async def run_cli(task: str) -> None:
	"""Jalankan satu task langsung dari command line."""
	runner = AgentRunner(llm=LLM, config=AGENT_CONFIG)
	print(f'Task: {task}')
	print('-' * 60)
	result = await runner.run(task)
	print('-' * 60)
	print(result.format())


# ─── Mode: Telegram Bot ───────────────────────────────────────────────────────


async def run_telegram() -> None:
	"""Jalankan bot Telegram — terima perintah /task dari Telegram."""
	from channels.telegram import TelegramChannel

	token = os.getenv('TELEGRAM_BOT_TOKEN', '')
	if not token:
		print('ERROR: TELEGRAM_BOT_TOKEN belum diset di .env')
		sys.exit(1)

	# Parse allowed users dari env (kosong = semua diizinkan)
	allowed_raw = os.getenv('TELEGRAM_ALLOWED_USERS', '').strip()
	allowed_users = [int(uid.strip()) for uid in allowed_raw.split(',') if uid.strip().isdigit()]

	runner = AgentRunner(llm=LLM, config=AGENT_CONFIG)
	bot = TelegramChannel(
		runner=runner,
		token=token,
		allowed_users=allowed_users if allowed_users else None,
	)

	# Graceful shutdown saat Ctrl+C
	loop = asyncio.get_running_loop()

	def _shutdown():
		print('\nMenghentikan bot...')
		asyncio.create_task(bot.stop())

	for sig in (signal.SIGINT, signal.SIGTERM):
		try:
			loop.add_signal_handler(sig, _shutdown)
		except (NotImplementedError, OSError):
			# Windows tidak support add_signal_handler sepenuhnya
			pass

	print('Telegram bot berjalan. Tekan Ctrl+C untuk berhenti.')
	await bot.start()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
	args = sys.argv[1:]

	if '--telegram' in args:
		asyncio.run(run_telegram())

	else:
		# CLI mode: ambil task dari argumen atau default
		if args:
			task = ' '.join(args)
		else:
			task = 'Go to google.com and return the page title'
		asyncio.run(run_cli(task))
