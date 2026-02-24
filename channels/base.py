"""
Channel Modular - Base Interface (Plug & Play)

Cara menambahkan channel baru:
1. Buat folder baru di channels/<nama_channel>/
2. Buat class yang extends BaseChannel
3. Implementasikan method abstract: start(), stop()
4. Register di channels/__init__.py

Contoh channel yang sudah ada:
- channels/telegram/ → TelegramChannel
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Awaitable

from browser_use.agent.service import Agent
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.llm import BaseChatModel
from browser_use import Tools
from browser_use.agent.views import ActionResult

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
	"""Konfigurasi untuk menjalankan browser agent."""

	max_steps: int = 50
	headless: bool = True
	executable_path: str = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
	extra_agent_kwargs: dict = field(default_factory=dict)


@dataclass
class StepUpdate:
	"""Data satu langkah agent yang dikirim ke channel via callback."""

	step: int
	max_steps: int
	action_names: list[str]        # nama aksi yang dijalankan (navigate, click, dll)
	next_goal: str                  # tujuan langkah berikutnya
	evaluation: str                 # evaluasi langkah sebelumnya


@dataclass
class TaskResult:
	"""Hasil eksekusi task dari agent."""

	success: bool
	output: str
	steps: int
	errors: list[str]
	attachments: list[str] = field(default_factory=list)  # path file screenshot/attachment

	def format(self) -> str:
		"""Format hasil untuk dikirim balik ke channel."""
		status = 'Selesai' if self.success else 'Gagal'
		lines = [
			f'Status: {status}',
			f'Langkah: {self.steps}',
			f'',
			f'Hasil:',
			f'{self.output}',
		]
		if self.errors and any(e for e in self.errors if e):
			lines += ['', 'Error:', *[f'- {e}' for e in self.errors if e]]
		return '\n'.join(lines)


# Type alias untuk step callback
StepCallback = Callable[[StepUpdate], Awaitable[None]]


def _build_screenshot_tools() -> Tools:
	"""
	Buat Tools dengan screenshot action yang SELALU menyimpan ke disk.

	Masalah default: Agent memanggil screenshot tanpa file_name → hanya
	set metadata untuk observasi berikutnya, tidak ada file yang disimpan.
	Fix: Override screenshot action agar selalu simpan ke file bertimestamp.
	"""
	tools = Tools()

	# Hapus screenshot default lalu tambah versi kita
	# (exclude_actions tidak bisa dipakai karena Tools() sudah init;
	#  kita daftarkan action baru dengan nama yang sama — yang terakhir menang)
	@tools.action(
		'Take a screenshot of the current page and save it to disk. '
		'Always saves to a PNG file and returns the file path as an attachment.'
	)
	async def screenshot(browser_session: BrowserSession) -> ActionResult:
		"""Take screenshot dan simpan ke disk dengan nama bertimestamp."""
		import tempfile
		ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
		file_name = f'screenshot_{ts}.png'
		save_dir = Path(tempfile.gettempdir()) / 'mybrowse_screenshots'
		save_dir.mkdir(parents=True, exist_ok=True)
		file_path = save_dir / file_name

		screenshot_bytes = await browser_session.take_screenshot(full_page=False)
		file_path.write_bytes(screenshot_bytes)

		result = f'Screenshot saved to {file_path}'
		logger.info(f'Screenshot: {file_path}')
		return ActionResult(
			extracted_content=result,
			long_term_memory=result,
			attachments=[str(file_path)],
		)

	return tools


class AgentRunner:
	"""Runner terpusat untuk menjalankan browser agent dari channel manapun."""

	def __init__(self, llm: BaseChatModel, config: AgentConfig | None = None):
		self.llm = llm
		self.config = config or AgentConfig()
		self._lock = asyncio.Lock()

	async def run(
		self,
		task: str,
		on_step: StepCallback | None = None,
		# ── DB context (opsional, diisi oleh channel) ──
		channel: str | None = None,
		channel_id: str | None = None,
		username: str | None = None,
		memory_context: str | None = None,
	) -> TaskResult:
		"""
		Jalankan task menggunakan browser agent.

		Args:
		    task: Deskripsi task yang akan dijalankan
		    on_step: Async callback dipanggil setiap kali agent menyelesaikan satu langkah.
		             Menerima StepUpdate berisi info step terkini.
		    channel: Nama channel (e.g. 'telegram')
		    channel_id: ID chat/user di channel tersebut
		    username: Username pengguna
		    memory_context: String long-term memory yang di-inject ke prompt
		"""
		# Import db di sini agar opsional (tidak crash jika DB tidak tersedia)
		use_db = bool(channel and channel_id)
		db_mod = None
		task_id: str | None = None

		if use_db:
			try:
				import db as db_mod
			except ImportError:
				use_db = False
				logger.warning('db module tidak ditemukan, melewati logging DB')

		async with self._lock:
			start_ts = time.time()

			# Inject memory context ke task prompt jika ada
			full_task = task
			if memory_context:
				full_task = f'{memory_context}\n\n---\nTask sekarang:\n{task}'

			browser_profile = BrowserProfile(
				headless=self.config.headless,
				executable_path=self.config.executable_path,
			)
			browser_session = BrowserSession(browser_profile=browser_profile)

			# Buat task DB record
			if use_db and db_mod:
				try:
					task_id = await db_mod.task_create(
						channel=channel,
						channel_id=channel_id,
						prompt=task,
						username=username,
					)
				except Exception as e:
					logger.warning(f'DB task_create gagal (non-fatal): {e}')
					task_id = None

			# Buat step callback wrapper yang dikenali Agent
			async def _step_cb(browser_state: Any, agent_output: Any, step_num: int) -> None:
				# Set task ke RUNNING pada step pertama
				if step_num == 1 and use_db and db_mod and task_id:
					try:
						await db_mod.task_start(task_id)
					except Exception as e:
						logger.debug(f'DB task_start gagal: {e}')

				# Ambil nama aksi dari AgentOutput
				action_names: list[str] = []
				for action in agent_output.action:
					d = action.model_dump(exclude_none=True)
					name = 'unknown'
					for k in d:
						if k != 'index':
							name = k
							break
					action_names.append(name)

				# Log step ke DB
				if use_db and db_mod and task_id:
					try:
						url = ''
						try:
							url = browser_state.url or ''
						except Exception:
							pass
						await db_mod.step_log(
							task_id=task_id,
							step_num=step_num,
							actions=action_names,
							next_goal=agent_output.next_goal or '',
							evaluation=agent_output.evaluation_previous_goal or '',
							url=url,
						)
					except Exception as e:
						logger.debug(f'DB step_log gagal: {e}')

				if on_step is None:
					return
				try:
					update = StepUpdate(
						step=step_num,
						max_steps=self.config.max_steps,
						action_names=action_names,
						next_goal=agent_output.next_goal or '',
						evaluation=agent_output.evaluation_previous_goal or '',
					)
					await on_step(update)
				except Exception as e:
					logger.debug(f'Step callback error (non-fatal): {e}')

			tools = _build_screenshot_tools()

			agent = Agent(
				task=full_task,
				llm=self.llm,
				browser_session=browser_session,
				tools=tools,
				register_new_step_callback=_step_cb,
				**self.config.extra_agent_kwargs,
			)

			try:
				result = await agent.run(max_steps=self.config.max_steps)
				output = result.final_result() or 'Task selesai tanpa output.'
				errors = [e for e in result.errors() if e]

				# Kumpulkan semua attachment (path file screenshot, dll) dari seluruh history
				attachments: list[str] = []
				for action_result in result.action_results():
					if action_result and action_result.attachments:
						for path in action_result.attachments:
							if path and path not in attachments:
								attachments.append(str(path))

				duration_ms = int((time.time() - start_ts) * 1000)
				success = result.is_successful() is not False

				# Selesaikan task di DB
				if use_db and db_mod and task_id:
					try:
						await db_mod.task_done(
							task_id=task_id,
							output=output,
							success=success,
							steps=result.number_of_steps(),
							duration_ms=duration_ms,
						)
						# Simpan attachment ke DB
						for path in attachments:
							try:
								p = Path(path)
								size = p.stat().st_size if p.exists() else None
								ext = p.suffix.lower()
								ftype = 'screenshot' if ext in ('.png', '.jpg', '.jpeg', '.webp') else 'file'
								mime = 'image/png' if ext == '.png' else ('image/jpeg' if ext in ('.jpg', '.jpeg') else None)
								await db_mod.attachment_save(
									task_id=task_id,
									file_name=p.name,
									file_path=path,
									file_type=ftype,
									mime_type=mime,
									size_bytes=size,
								)
							except Exception as e:
								logger.debug(f'DB attachment_save gagal: {e}')
					except Exception as e:
						logger.warning(f'DB task_done gagal (non-fatal): {e}')

				return TaskResult(
					success=success,
					output=output,
					steps=result.number_of_steps(),
					errors=errors,
					attachments=attachments,
				)
			except asyncio.CancelledError:
				# Task dibatalkan oleh user
				if use_db and db_mod and task_id:
					try:
						await db_mod.task_cancel(task_id)
					except Exception:
						pass
				raise
			except Exception as e:
				logger.exception(f'Agent error: {e}')
				duration_ms = int((time.time() - start_ts) * 1000)
				if use_db and db_mod and task_id:
					try:
						await db_mod.task_done(
							task_id=task_id,
							output='',
							success=False,
							steps=0,
							duration_ms=duration_ms,
						)
					except Exception:
						pass
				return TaskResult(
					success=False,
					output='',
					steps=0,
					errors=[str(e)],
				)


class BaseChannel(ABC):
	"""
	Abstract base class untuk semua channel (Telegram, WhatsApp, Discord, dll).

	Implementasikan start() dan stop() di subclass masing-masing channel.
	AgentRunner sudah disediakan dan siap dipakai via self.runner.run(task).
	"""

	def __init__(self, runner: AgentRunner, **kwargs: Any):
		self.runner = runner
		self.logger = logging.getLogger(self.__class__.__name__)

	@abstractmethod
	async def start(self) -> None:
		"""Mulai channel (polling, webhook server, dll)."""
		...

	@abstractmethod
	async def stop(self) -> None:
		"""Hentikan channel dengan graceful shutdown."""
		...

	async def on_task(
		self,
		task: str,
		on_step: StepCallback | None = None,
		channel: str | None = None,
		channel_id: str | None = None,
		username: str | None = None,
		memory_context: str | None = None,
		**context: Any,
	) -> TaskResult:
		"""
		Hook utama: dipanggil saat ada task masuk dari channel.
		Override jika perlu pre/post processing, atau langsung pakai runner.
		"""
		self.logger.info(f'Task diterima: {task[:80]}...' if len(task) > 80 else f'Task diterima: {task}')
		return await self.runner.run(
			task,
			on_step=on_step,
			channel=channel,
			channel_id=channel_id,
			username=username,
			memory_context=memory_context,
		)
