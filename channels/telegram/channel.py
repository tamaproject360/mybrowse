"""
Telegram Channel untuk mybrowse.

Fitur:
- Quick Action menu (inline keyboard) di setiap pesan
- Animasi typing saat bot sedang memproses
- Live progress bar + step update di Telegram saat agent berjalan
- Edit-in-place pesan progress (tidak spam baru)
- Whitelist user_id untuk keamanan
- Cancel task yang sedang berjalan

Setup:
1. Buat bot baru via @BotFather di Telegram -> dapatkan TELEGRAM_BOT_TOKEN
2. Dapatkan chat_id kamu via @userinfobot atau /start bot ini
3. Tambahkan ke .env:
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_ALLOWED_USERS=123456789,987654321  # kosong = semua user diizinkan

Cara pakai di Telegram:
  /start         -> menu utama + quick actions
  /task <cmd>    -> jalankan browser task
  /status        -> cek status bot & task aktif
  /cancel        -> batalkan task yang sedang berjalan
  /help          -> bantuan
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from channels.base import AgentRunner, BaseChannel, StepUpdate, TaskResult

logger = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'

# ─── Progress bar helper ──────────────────────────────────────────────────────

def make_progress_bar(current: int, total: int, width: int = 16) -> str:
	"""Buat progress bar ASCII. Contoh: [████████░░░░░░░░] 8/20"""
	filled = int(width * current / max(total, 1))
	bar = '█' * filled + '░' * (width - filled)
	return f'[{bar}] {current}/{total}'


def make_step_icon(action_names: list[str]) -> str:
	"""Pilih emoji sesuai tipe aksi yang sedang dijalankan."""
	icon_map = {
		'navigate': '🌐',
		'click': '🖱',
		'input': '⌨️',
		'scroll': '📜',
		'extract': '📋',
		'search': '🔍',
		'screenshot': '📸',
		'evaluate': '⚙️',
		'wait': '⏳',
		'done': '✅',
		'write_file': '💾',
		'read_file': '📂',
		'send_keys': '⌨️',
	}
	for name in action_names:
		for key, icon in icon_map.items():
			if key in name.lower():
				return icon
	return '⚡'


# ─── Session state per-chat ───────────────────────────────────────────────────

@dataclass
class ChatSession:
	"""State untuk setiap chat aktif."""

	chat_id: int
	task_coroutine: asyncio.Task | None = None
	progress_msg_id: int | None = None    # ID pesan progress yang di-edit
	step_count: int = 0
	start_time: float = field(default_factory=time.time)
	last_step_update: float = 0.0         # throttle edit agar tidak flood API


# ─── TelegramChannel ─────────────────────────────────────────────────────────

class TelegramChannel(BaseChannel):
	"""
	Channel Telegram dengan live progress, quick actions, dan animasi.

	Fitur utama:
	- Inline keyboard quick actions di /start dan /help
	- Pesan progress ter-edit secara live (tidak spam)
	- Progress bar ASCII + step counter + nama aksi + next goal
	- Animasi typing indicator selama agent berpikir
	- Cancel task yang sedang berjalan
	"""

	def __init__(
		self,
		runner: AgentRunner,
		token: str,
		allowed_users: list[int] | None = None,
		poll_timeout: int = 30,
		progress_edit_interval: float = 2.0,   # edit pesan progress max setiap N detik
		**kwargs: Any,
	):
		super().__init__(runner, **kwargs)
		self.token = token
		self.allowed_users = set(allowed_users) if allowed_users else None
		self.poll_timeout = poll_timeout
		self.progress_edit_interval = progress_edit_interval
		self._offset = 0
		self._running = False
		self._session: aiohttp.ClientSession | None = None
		self._chats: dict[int, ChatSession] = {}   # chat_id -> session

	# ─── Telegram API helpers ─────────────────────────────────────────────

	def _url(self, method: str) -> str:
		return TELEGRAM_API.format(token=self.token, method=method)

	async def _api(self, method: str, **params: Any) -> dict:
		assert self._session is not None
		try:
			async with self._session.post(self._url(method), json=params) as resp:
				data = await resp.json()
				if not data.get('ok'):
					desc = data.get('description', 'unknown error')
					# Jangan raise untuk error yang tidak kritis
					if 'message is not modified' not in desc:
						logger.warning(f'Telegram API [{method}]: {desc}')
					return {}
				return data.get('result', {})
		except Exception as e:
			logger.error(f'Telegram API request failed [{method}]: {e}')
			return {}

	async def _send(
		self,
		chat_id: int,
		text: str,
		reply_to: int | None = None,
		keyboard: list[list[dict]] | None = None,
		parse_mode: str = 'HTML',
	) -> int | None:
		"""Kirim pesan, return message_id."""
		max_len = 4096
		msg_id = None
		chunks = [text[i: i + max_len] for i in range(0, max(len(text), 1), max_len)]
		for i, chunk in enumerate(chunks):
			params: dict[str, Any] = {
				'chat_id': chat_id,
				'text': chunk,
				'parse_mode': parse_mode,
			}
			if reply_to and i == 0:
				params['reply_to_message_id'] = reply_to
			if keyboard and i == len(chunks) - 1:
				params['reply_markup'] = {'inline_keyboard': keyboard}
			result = await self._api('sendMessage', **params)
			if i == 0:
				msg_id = result.get('message_id')
		return msg_id

	async def _edit(
		self,
		chat_id: int,
		message_id: int,
		text: str,
		keyboard: list[list[dict]] | None = None,
		parse_mode: str = 'HTML',
	) -> bool:
		"""Edit pesan existing, return True jika berhasil."""
		params: dict[str, Any] = {
			'chat_id': chat_id,
			'message_id': message_id,
			'text': text[:4096],
			'parse_mode': parse_mode,
		}
		if keyboard is not None:
			params['reply_markup'] = {'inline_keyboard': keyboard}
		result = await self._api('editMessageText', **params)
		return bool(result)

	async def _answer_callback(self, callback_query_id: str, text: str = '') -> None:
		"""Jawab callback query dari inline button (hapus loading spinner)."""
		await self._api('answerCallbackQuery', callback_query_id=callback_query_id, text=text)

	async def _typing(self, chat_id: int) -> None:
		"""Kirim action 'typing'."""
		await self._api('sendChatAction', chat_id=chat_id, action='typing')

	async def _send_photo(
		self,
		chat_id: int,
		photo_path: str,
		caption: str = '',
		keyboard: list[list[dict]] | None = None,
	) -> int | None:
		"""
		Kirim file gambar ke Telegram menggunakan multipart/form-data.
		Mendukung PNG, JPEG, WebP. Max 10MB untuk foto, 50MB untuk document.
		"""
		assert self._session is not None
		path = Path(photo_path)
		if not path.exists():
			logger.warning(f'File screenshot tidak ditemukan: {photo_path}')
			return None

		file_size = path.stat().st_size
		# Gunakan sendDocument untuk file besar (>10MB) agar kualitas tidak dikompres
		use_document = file_size > 10 * 1024 * 1024

		try:
			form = aiohttp.FormData()
			form.add_field('chat_id', str(chat_id))
			if caption:
				form.add_field('caption', caption[:1024])
				form.add_field('parse_mode', 'HTML')
			if keyboard:
				import json
				form.add_field('reply_markup', json.dumps({'inline_keyboard': keyboard}))

			with open(photo_path, 'rb') as f:
				field_name = 'document' if use_document else 'photo'
				form.add_field(
					field_name,
					f,
					filename=path.name,
					content_type='image/png' if path.suffix.lower() == '.png' else 'image/jpeg',
				)
				method = 'sendDocument' if use_document else 'sendPhoto'
				async with self._session.post(self._url(method), data=form) as resp:
					data = await resp.json()
					if data.get('ok'):
						result = data.get('result', {})
						return result.get('message_id')
					else:
						logger.error(f'Gagal kirim foto [{method}]: {data.get("description")}')
						return None
		except Exception as e:
			logger.error(f'Error saat kirim foto: {e}')
			return None

	async def _send_photos_batch(
		self,
		chat_id: int,
		photo_paths: list[str],
		caption: str = '',
	) -> None:
		"""Kirim multiple screenshot satu per satu ke Telegram."""
		valid_paths = [p for p in photo_paths if Path(p).exists()]
		if not valid_paths:
			return

		for i, path in enumerate(valid_paths):
			cap = caption if i == 0 else ''  # caption hanya di foto pertama
			await self._send_photo(chat_id, path, caption=cap)
			if len(valid_paths) > 1:
				await asyncio.sleep(0.3)  # jeda kecil agar tidak flood

	async def _send_document(self, chat_id: int, file_path: str, caption: str = '') -> None:
		"""Kirim file arbitrary sebagai document Telegram."""
		assert self._session is not None
		path = Path(file_path)
		if not path.exists():
			return
		try:
			form = aiohttp.FormData()
			form.add_field('chat_id', str(chat_id))
			if caption:
				form.add_field('caption', caption[:1024])
			with open(file_path, 'rb') as f:
				form.add_field('document', f, filename=path.name)
				async with self._session.post(self._url('sendDocument'), data=form) as resp:
					data = await resp.json()
					if not data.get('ok'):
						logger.error(f'Gagal kirim document: {data.get("description")}')
		except Exception as e:
			logger.error(f'Error saat kirim document: {e}')

	# ─── Keyboard layouts ─────────────────────────────────────────────────

	def _main_keyboard(self) -> list[list[dict]]:
		"""Inline keyboard menu utama."""
		return [
			[
				{'text': '📊 Status', 'callback_data': 'cmd:status'},
				{'text': '❓ Help', 'callback_data': 'cmd:help'},
			],
			[
				{'text': '🚫 Cancel Task', 'callback_data': 'cmd:cancel'},
			],
		]

	def _task_keyboard(self) -> list[list[dict]]:
		"""Keyboard saat task sedang berjalan."""
		return [
			[
				{'text': '🚫 Cancel Task', 'callback_data': 'cmd:cancel'},
				{'text': '📊 Status', 'callback_data': 'cmd:status'},
			],
		]

	def _done_keyboard(self) -> list[list[dict]]:
		"""Keyboard setelah task selesai."""
		return [
			[
				{'text': '📊 Status', 'callback_data': 'cmd:status'},
				{'text': '❓ Help', 'callback_data': 'cmd:help'},
			],
		]

	# ─── Progress message ─────────────────────────────────────────────────

	def _build_progress_text(
		self,
		task: str,
		session: ChatSession,
		step: StepUpdate | None = None,
		done: bool = False,
	) -> str:
		elapsed = int(time.time() - session.start_time)
		mins, secs = divmod(elapsed, 60)
		elapsed_str = f'{mins}m {secs}s' if mins else f'{secs}s'

		task_preview = task[:60] + '...' if len(task) > 60 else task

		if done:
			return (
				f'<b>✅ Task Selesai</b>\n'
				f'<code>{task_preview}</code>\n\n'
				f'Total langkah: <b>{session.step_count}</b>\n'
				f'Waktu: <b>{elapsed_str}</b>'
			)

		if step is None:
			return (
				f'<b>⏳ Memulai task...</b>\n'
				f'<code>{task_preview}</code>\n\n'
				f'Menginisialisasi browser agent...'
			)

		bar = make_progress_bar(step.step, step.max_steps)
		icon = make_step_icon(step.action_names)
		actions_str = ', '.join(step.action_names) if step.action_names else 'memproses'

		lines = [
			f'<b>🤖 Agent Berjalan</b>',
			f'<code>{task_preview}</code>',
			f'',
			f'<b>Progress:</b> {bar}',
			f'<b>Langkah:</b> {step.step} dari maks {step.max_steps}',
			f'<b>Waktu:</b> {elapsed_str}',
			f'',
			f'{icon} <b>Aksi:</b> <code>{actions_str}</code>',
		]

		if step.evaluation:
			# Potong evaluasi agar tidak terlalu panjang
			eval_short = step.evaluation[:120] + '...' if len(step.evaluation) > 120 else step.evaluation
			lines.append(f'<b>Evaluasi:</b> {eval_short}')

		if step.next_goal:
			goal_short = step.next_goal[:120] + '...' if len(step.next_goal) > 120 else step.next_goal
			lines.append(f'<b>Tujuan:</b> {goal_short}')

		return '\n'.join(lines)

	# ─── Chat session helpers ─────────────────────────────────────────────

	def _get_session(self, chat_id: int) -> ChatSession:
		if chat_id not in self._chats:
			self._chats[chat_id] = ChatSession(chat_id=chat_id)
		return self._chats[chat_id]

	def _has_active_task(self, chat_id: int) -> bool:
		s = self._chats.get(chat_id)
		return s is not None and s.task_coroutine is not None and not s.task_coroutine.done()

	# ─── Access control ───────────────────────────────────────────────────

	def _is_allowed(self, chat_id: int) -> bool:
		if self.allowed_users is None:
			return True
		return chat_id in self.allowed_users

	# ─── Command handlers ─────────────────────────────────────────────────

	async def _cmd_start(self, chat_id: int, msg_id: int, username: str) -> None:
		text = (
			f'Halo <b>{username}</b>! 👋\n\n'
			f'Saya <b>mybrowse bot</b> — browser agent yang bisa kamu kendalikan dari Telegram.\n\n'
			f'<b>Cara pakai:</b>\n'
			f'Kirim perintah dengan format:\n'
			f'<code>/task perintah yang ingin dijalankan</code>\n\n'
			f'<b>Contoh:</b>\n'
			f'<code>/task buka google.com dan cari harga iPhone terbaru</code>\n'
			f'<code>/task buka tokopedia.com dan cari laptop gaming termurah</code>\n\n'
			f'Gunakan tombol di bawah untuk navigasi cepat:'
		)
		await self._send(chat_id, text, reply_to=msg_id, keyboard=self._main_keyboard())

	async def _cmd_help(self, chat_id: int, msg_id: int) -> None:
		text = (
			f'<b>mybrowse — Panduan Penggunaan</b>\n\n'
			f'<b>Perintah:</b>\n'
			f'  /task &lt;perintah&gt; — jalankan browser task\n'
			f'  /cancel            — batalkan task yang berjalan\n'
			f'  /status            — cek status bot\n'
			f'  /help              — tampilkan bantuan ini\n\n'
			f'<b>Contoh task:</b>\n'
			f'<code>/task cari harga iPhone 16 di tokopedia</code>\n'
			f'<code>/task buka github.com/browser-use/browser-use dan lihat berapa stars</code>\n'
			f'<code>/task buka google.com dan cari berita AI terbaru</code>\n\n'
			f'<b>Tips:</b>\n'
			f'• Semakin spesifik perintahmu, semakin akurat hasilnya\n'
			f'• Satu task berjalan pada satu waktu\n'
			f'• Gunakan /cancel untuk membatalkan'
		)
		await self._send(chat_id, text, reply_to=msg_id, keyboard=self._main_keyboard())

	async def _cmd_status(self, chat_id: int, msg_id: int) -> None:
		active_count = sum(1 for s in self._chats.values() if self._has_active_task(s.chat_id))
		is_busy = self._has_active_task(chat_id)

		if is_busy:
			session = self._get_session(chat_id)
			elapsed = int(time.time() - session.start_time)
			mins, secs = divmod(elapsed, 60)
			elapsed_str = f'{mins}m {secs}s' if mins else f'{secs}s'
			status_text = (
				f'<b>📊 Status Bot</b>\n\n'
				f'Status: <b>🔄 Task sedang berjalan</b>\n'
				f'Langkah selesai: <b>{session.step_count}</b>\n'
				f'Waktu berjalan: <b>{elapsed_str}</b>\n\n'
				f'Gunakan /cancel untuk menghentikan.'
			)
		else:
			status_text = (
				f'<b>📊 Status Bot</b>\n\n'
				f'Status: <b>✅ Siap menerima task</b>\n'
				f'Total chat aktif: <b>{active_count}</b>\n\n'
				f'Kirim /task untuk memulai.'
			)
		await self._send(chat_id, status_text, reply_to=msg_id, keyboard=self._main_keyboard())

	async def _cmd_cancel(self, chat_id: int, msg_id: int) -> None:
		session = self._get_session(chat_id)
		if session.task_coroutine and not session.task_coroutine.done():
			session.task_coroutine.cancel()
			await self._send(chat_id, '🚫 Membatalkan task...', reply_to=msg_id)
		else:
			await self._send(
				chat_id,
				'Tidak ada task yang sedang berjalan.',
				reply_to=msg_id,
				keyboard=self._main_keyboard(),
			)

	async def _cmd_task(self, chat_id: int, msg_id: int, task: str, username: str) -> None:
		"""Terima dan jalankan task baru."""
		if not task:
			await self._send(
				chat_id,
				'Gunakan: <code>/task perintah yang ingin dijalankan</code>',
				reply_to=msg_id,
				keyboard=self._main_keyboard(),
			)
			return

		if self._has_active_task(chat_id):
			await self._send(
				chat_id,
				'Ada task yang sedang berjalan. Gunakan /cancel untuk membatalkan terlebih dahulu.',
				reply_to=msg_id,
				keyboard=self._task_keyboard(),
			)
			return

		# Reset session
		session = self._get_session(chat_id)
		session.step_count = 0
		session.start_time = time.time()
		session.last_step_update = 0.0
		session.progress_msg_id = None

		# Kirim pesan progress awal (akan di-edit terus)
		progress_text = self._build_progress_text(task, session, step=None)
		prog_msg_id = await self._send(
			chat_id,
			progress_text,
			reply_to=msg_id,
			keyboard=self._task_keyboard(),
		)
		session.progress_msg_id = prog_msg_id
		self.logger.info(f'[{username}] Task dimulai: {task}')

		# ─── Step callback: update pesan progress secara live ─────────────
		async def on_step(update: StepUpdate) -> None:
			session.step_count = update.step
			now = time.time()

			# Throttle: edit max setiap progress_edit_interval detik
			if now - session.last_step_update < self.progress_edit_interval:
				return
			session.last_step_update = now

			if session.progress_msg_id:
				new_text = self._build_progress_text(task, session, step=update)
				await self._edit(
					chat_id,
					session.progress_msg_id,
					new_text,
					keyboard=self._task_keyboard(),
				)

		# ─── Jalankan agent di background task ────────────────────────────
		async def run_and_reply() -> None:
			# Typing loop berjalan paralel selama agent aktif
			typing_stop = asyncio.Event()

			async def typing_loop() -> None:
				while not typing_stop.is_set():
					await self._typing(chat_id)
					try:
						await asyncio.wait_for(typing_stop.wait(), timeout=4.0)
					except asyncio.TimeoutError:
						pass

			typing_task = asyncio.create_task(typing_loop())

			try:
				result: TaskResult = await self.on_task(task, on_step=on_step)
				typing_stop.set()
				typing_task.cancel()

				# Update pesan progress jadi "selesai"
				if session.progress_msg_id:
					done_text = self._build_progress_text(task, session, done=True)
					await self._edit(chat_id, session.progress_msg_id, done_text, keyboard=[])

				# Kirim hasil akhir sebagai pesan teks
				status_icon = '✅' if result.success else '❌'
				result_text = (
					f'{status_icon} <b>Hasil Task</b>\n\n'
					f'{result.format()}'
				)
				await self._send(chat_id, result_text, keyboard=self._done_keyboard())

				# Kirim screenshot / attachment jika ada
				if result.attachments:
					screenshot_paths = [
						p for p in result.attachments
						if p.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
					]
					other_files = [
						p for p in result.attachments
						if p not in screenshot_paths
					]

					if screenshot_paths:
						caption = f'📸 <b>Screenshot</b> ({len(screenshot_paths)} gambar)'
						await self._send_photos_batch(chat_id, screenshot_paths, caption=caption)

					# File non-gambar: kirim sebagai document
					for file_path in other_files:
						if Path(file_path).exists():
							await self._send_document(chat_id, file_path)

			except asyncio.CancelledError:
				typing_stop.set()
				typing_task.cancel()
				if session.progress_msg_id:
					cancel_text = (
						f'<b>🚫 Task Dibatalkan</b>\n'
						f'<code>{task[:60]}</code>\n\n'
						f'Langkah selesai: {session.step_count}'
					)
					await self._edit(chat_id, session.progress_msg_id, cancel_text, keyboard=[])
				await self._send(chat_id, 'Task telah dibatalkan.', keyboard=self._main_keyboard())

			except Exception as e:
				typing_stop.set()
				typing_task.cancel()
				self.logger.exception(f'Error saat menjalankan task: {e}')
				await self._send(chat_id, f'❌ Error: <code>{e}</code>', keyboard=self._main_keyboard())

			finally:
				session.task_coroutine = None

		coro = asyncio.create_task(run_and_reply())
		session.task_coroutine = coro

	# ─── Update dispatcher ────────────────────────────────────────────────

	async def _handle_update(self, update: dict) -> None:
		"""Dispatch satu update Telegram ke handler yang sesuai."""

		# ── Callback query (tombol inline keyboard ditekan) ──────────────
		if 'callback_query' in update:
			cq = update['callback_query']
			cq_id = cq['id']
			chat_id = cq['message']['chat']['id']
			msg_id = cq['message']['message_id']
			data = cq.get('data', '')
			username = cq.get('from', {}).get('username', str(chat_id))

			await self._answer_callback(cq_id)

			if not self._is_allowed(chat_id):
				return

			if data == 'cmd:status':
				await self._cmd_status(chat_id, msg_id)
			elif data == 'cmd:help':
				await self._cmd_help(chat_id, msg_id)
			elif data == 'cmd:cancel':
				await self._cmd_cancel(chat_id, msg_id)
			return

		# ── Regular message ───────────────────────────────────────────────
		message = update.get('message') or update.get('edited_message')
		if not message:
			return

		chat_id: int = message['chat']['id']
		msg_id: int = message['message_id']
		text: str = message.get('text', '').strip()
		username: str = message.get('from', {}).get('username', str(chat_id))

		if not text:
			return

		if not self._is_allowed(chat_id):
			await self._send(chat_id, '⛔ Akses ditolak.', reply_to=msg_id)
			logger.warning(f'Akses ditolak: {username} ({chat_id})')
			return

		# ── Parse perintah ────────────────────────────────────────────────
		# Pisahkan command dari @botname suffix (e.g. /task@mybrowsebot ...)
		cmd_raw = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''

		if cmd_raw == '/start':
			await self._cmd_start(chat_id, msg_id, username)

		elif cmd_raw == '/help':
			await self._cmd_help(chat_id, msg_id)

		elif cmd_raw == '/status':
			await self._cmd_status(chat_id, msg_id)

		elif cmd_raw == '/cancel':
			await self._cmd_cancel(chat_id, msg_id)

		elif cmd_raw == '/task':
			task = text[len(cmd_raw):].strip()
			# Hilangkan @botname jika ada di cmd_raw asli
			if '@' in text.split()[0]:
				parts = text.split(' ', 1)
				task = parts[1].strip() if len(parts) > 1 else ''
			await self._cmd_task(chat_id, msg_id, task, username)

		elif not text.startswith('/'):
			# Pesan biasa (bukan command) -> tanya apakah mau dijalankan sebagai task
			hint = (
				f'Mau menjalankan ini sebagai task?\n\n'
				f'<code>/task {text[:200]}</code>'
			)
			await self._send(chat_id, hint, reply_to=msg_id, keyboard=self._main_keyboard())

	# ─── Lifecycle ────────────────────────────────────────────────────────

	async def start(self) -> None:
		"""Mulai long polling Telegram."""
		self._session = aiohttp.ClientSession()
		self._running = True

		# Verifikasi token
		me = await self._api('getMe')
		if not me:
			await self._session.close()
			raise RuntimeError('Gagal terhubung ke Telegram. Periksa TELEGRAM_BOT_TOKEN.')

		bot_name = me.get('username', 'unknown')
		self.logger.info(f'Telegram bot @{bot_name} terhubung. Mulai polling...')

		# Set bot commands agar muncul di menu Telegram
		await self._api(
			'setMyCommands',
			commands=[
				{'command': 'start', 'description': 'Menu utama'},
				{'command': 'task', 'description': 'Jalankan browser task'},
				{'command': 'status', 'description': 'Cek status bot'},
				{'command': 'cancel', 'description': 'Batalkan task yang berjalan'},
				{'command': 'help', 'description': 'Bantuan penggunaan'},
			],
		)

		# Flush pending updates lama
		try:
			await self._api('getUpdates', offset=-1, timeout=1)
		except Exception:
			pass

		# Long polling loop
		while self._running:
			try:
				updates = await self._api(
					'getUpdates',
					offset=self._offset,
					timeout=self.poll_timeout,
					allowed_updates=['message', 'edited_message', 'callback_query'],
				)
				if isinstance(updates, list):
					for update in updates:
						self._offset = update['update_id'] + 1
						asyncio.create_task(self._handle_update(update))
			except asyncio.CancelledError:
				break
			except Exception as e:
				if self._running:
					self.logger.error(f'Polling error: {e}')
					await asyncio.sleep(5)

		await self._cleanup()

	async def stop(self) -> None:
		"""Graceful shutdown."""
		self.logger.info('Menghentikan Telegram bot...')
		self._running = False
		for session in self._chats.values():
			if session.task_coroutine and not session.task_coroutine.done():
				session.task_coroutine.cancel()
		await self._cleanup()

	async def _cleanup(self) -> None:
		if self._session and not self._session.closed:
			await self._session.close()
