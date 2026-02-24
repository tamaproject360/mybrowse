"""
Telegram Channel untuk mybrowse.

Setup:
1. Buat bot baru via @BotFather di Telegram → dapatkan TELEGRAM_BOT_TOKEN
2. Dapatkan chat_id kamu via @userinfobot atau bot lain
3. Tambahkan ke .env:
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_ALLOWED_USERS=123456789,987654321  # chat_id yang diizinkan (kosong = semua)

Cara pakai di Telegram:
  /task <perintah>    → jalankan browser task
  /status             → cek apakah bot aktif
  /help               → tampilkan bantuan

Contoh:
  /task buka google.com dan cari berita AI terbaru
  /task buka tokopedia.com dan cari laptop gaming termurah
"""

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

from channels.base import AgentRunner, BaseChannel, TaskResult

logger = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'


class TelegramChannel(BaseChannel):
	"""
	Channel Telegram menggunakan long polling (tidak butuh webhook/server publik).

	Mendukung:
	- /task <perintah>  → jalankan browser agent
	- /status           → cek status bot
	- /help             → bantuan
	- Whitelist user_id untuk keamanan
	"""

	def __init__(
		self,
		runner: AgentRunner,
		token: str,
		allowed_users: list[int] | None = None,
		poll_timeout: int = 30,
		**kwargs: Any,
	):
		"""
		Args:
		    runner: AgentRunner instance yang sudah dikonfigurasi
		    token: Telegram Bot Token dari @BotFather
		    allowed_users: List chat_id yang diizinkan. None = semua user diizinkan
		    poll_timeout: Timeout (detik) untuk long polling
		"""
		super().__init__(runner, **kwargs)
		self.token = token
		self.allowed_users = set(allowed_users) if allowed_users else None
		self.poll_timeout = poll_timeout
		self._offset = 0
		self._running = False
		self._session: aiohttp.ClientSession | None = None
		self._active_tasks: dict[int, asyncio.Task] = {}  # chat_id → running task

	def _url(self, method: str) -> str:
		return TELEGRAM_API.format(token=self.token, method=method)

	async def _api(self, method: str, **params: Any) -> dict:
		"""Panggil Telegram Bot API."""
		assert self._session is not None
		async with self._session.post(self._url(method), json=params) as resp:
			data = await resp.json()
			if not data.get('ok'):
				raise RuntimeError(f'Telegram API error [{method}]: {data.get("description")}')
			return data.get('result', {})

	async def _send(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
		"""Kirim pesan teks ke chat_id, potong jika terlalu panjang (max 4096 char)."""
		max_len = 4096
		chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
		for chunk in chunks:
			params: dict[str, Any] = {'chat_id': chat_id, 'text': chunk, 'parse_mode': 'HTML'}
			if reply_to:
				params['reply_to_message_id'] = reply_to
				reply_to = None  # hanya reply ke pesan pertama
			try:
				await self._api('sendMessage', **params)
			except Exception as e:
				logger.error(f'Gagal kirim pesan ke {chat_id}: {e}')

	async def _send_typing(self, chat_id: int) -> None:
		"""Kirim indikator 'sedang mengetik...'"""
		try:
			await self._api('sendChatAction', chat_id=chat_id, action='typing')
		except Exception:
			pass

	def _is_allowed(self, chat_id: int) -> bool:
		if self.allowed_users is None:
			return True
		return chat_id in self.allowed_users

	async def _handle_update(self, update: dict) -> None:
		"""Proses satu update dari Telegram."""
		message = update.get('message') or update.get('edited_message')
		if not message:
			return

		chat_id: int = message['chat']['id']
		msg_id: int = message['message_id']
		text: str = message.get('text', '').strip()
		username: str = message.get('from', {}).get('username', str(chat_id))

		if not text:
			return

		# Cek whitelist
		if not self._is_allowed(chat_id):
			await self._send(chat_id, 'Akses ditolak. Hubungi admin.', reply_to=msg_id)
			logger.warning(f'Akses ditolak untuk user {username} ({chat_id})')
			return

		# Parsing command
		if text.startswith('/task ') or text.startswith('/task@'):
			# Ambil task setelah /task
			task = text.split(' ', 1)[1].strip() if ' ' in text else ''
			if not task:
				await self._send(chat_id, 'Gunakan: <code>/task perintah yang ingin dijalankan</code>', reply_to=msg_id)
				return
			await self._handle_task(chat_id, msg_id, task, username)

		elif text == '/status' or text.startswith('/status@'):
			active = len(self._active_tasks)
			await self._send(
				chat_id,
				f'<b>mybrowse bot aktif</b>\nTask sedang berjalan: {active}',
				reply_to=msg_id,
			)

		elif text == '/help' or text.startswith('/help@'):
			await self._send(chat_id, self._help_text(), reply_to=msg_id)

		elif text == '/start' or text.startswith('/start@'):
			await self._send(
				chat_id,
				f'Halo <b>{username}</b>! Bot mybrowse siap.\n\n{self._help_text()}',
				reply_to=msg_id,
			)

		elif text == '/cancel' or text.startswith('/cancel@'):
			await self._handle_cancel(chat_id, msg_id)

	async def _handle_task(self, chat_id: int, msg_id: int, task: str, username: str) -> None:
		"""Jalankan browser task di background, kirim hasilnya saat selesai."""
		# Cek jika sudah ada task berjalan untuk chat ini
		if chat_id in self._active_tasks and not self._active_tasks[chat_id].done():
			await self._send(
				chat_id,
				'Ada task yang sedang berjalan. Gunakan /cancel untuk membatalkan.',
				reply_to=msg_id,
			)
			return

		await self._send(
			chat_id,
			f'Task diterima, sedang diproses...\n\n<code>{task}</code>',
			reply_to=msg_id,
		)
		self.logger.info(f'[{username}] Task: {task}')

		async def run_and_reply():
			# Kirim typing indicator periodik selama task berjalan
			async def keep_typing():
				while True:
					await self._send_typing(chat_id)
					await asyncio.sleep(4)

			typing_task = asyncio.create_task(keep_typing())
			try:
				result: TaskResult = await self.on_task(task)
				typing_task.cancel()
				await self._send(chat_id, f'<b>Hasil Task</b>\n\n{result.format()}')
			except asyncio.CancelledError:
				typing_task.cancel()
				await self._send(chat_id, 'Task dibatalkan.')
			except Exception as e:
				typing_task.cancel()
				self.logger.exception(f'Error saat menjalankan task: {e}')
				await self._send(chat_id, f'Error: {e}')
			finally:
				self._active_tasks.pop(chat_id, None)

		task_coro = asyncio.create_task(run_and_reply())
		self._active_tasks[chat_id] = task_coro

	async def _handle_cancel(self, chat_id: int, msg_id: int) -> None:
		"""Batalkan task yang sedang berjalan."""
		t = self._active_tasks.get(chat_id)
		if t and not t.done():
			t.cancel()
			await self._send(chat_id, 'Membatalkan task...', reply_to=msg_id)
		else:
			await self._send(chat_id, 'Tidak ada task yang sedang berjalan.', reply_to=msg_id)

	def _help_text(self) -> str:
		return (
			'<b>mybrowse - Browser Agent via Telegram</b>\n\n'
			'<b>Perintah:</b>\n'
			'  /task &lt;perintah&gt; — jalankan browser task\n'
			'  /cancel            — batalkan task yang berjalan\n'
			'  /status            — cek status bot\n'
			'  /help              — tampilkan bantuan ini\n\n'
			'<b>Contoh:</b>\n'
			'  <code>/task buka google.com dan cari harga iPhone terbaru</code>\n'
			'  <code>/task buka tokopedia.com dan cari laptop gaming termurah</code>'
		)

	async def start(self) -> None:
		"""Mulai long polling Telegram."""
		self._session = aiohttp.ClientSession()
		self._running = True

		# Verifikasi token
		try:
			me = await self._api('getMe')
			bot_name = me.get('username', 'unknown')
			self.logger.info(f'Telegram bot @{bot_name} terhubung, mulai polling...')
		except Exception as e:
			self.logger.error(f'Gagal terhubung ke Telegram: {e}')
			await self._session.close()
			raise

		# Hapus pending updates lama
		try:
			await self._api('getUpdates', offset=-1, timeout=1)
		except Exception:
			pass

		while self._running:
			try:
				updates = await self._api(
					'getUpdates',
					offset=self._offset,
					timeout=self.poll_timeout,
					allowed_updates=['message', 'edited_message'],
				)
				for update in updates:
					self._offset = update['update_id'] + 1
					asyncio.create_task(self._handle_update(update))
			except asyncio.CancelledError:
				break
			except Exception as e:
				if self._running:
					self.logger.error(f'Polling error: {e}')
					await asyncio.sleep(5)  # backoff sebelum retry

		await self._cleanup()

	async def stop(self) -> None:
		"""Hentikan bot dengan graceful shutdown."""
		self.logger.info('Menghentikan Telegram bot...')
		self._running = False
		# Cancel semua active tasks
		for t in self._active_tasks.values():
			if not t.done():
				t.cancel()
		await self._cleanup()

	async def _cleanup(self) -> None:
		if self._session and not self._session.closed:
			await self._session.close()
