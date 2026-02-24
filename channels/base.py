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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from browser_use.agent.service import Agent
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.llm import BaseChatModel

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
	) -> TaskResult:
		"""
		Jalankan task menggunakan browser agent.

		Args:
		    task: Deskripsi task yang akan dijalankan
		    on_step: Async callback dipanggil setiap kali agent menyelesaikan satu langkah.
		             Menerima StepUpdate berisi info step terkini.
		"""
		async with self._lock:
			browser_profile = BrowserProfile(
				headless=self.config.headless,
				executable_path=self.config.executable_path,
			)
			browser_session = BrowserSession(browser_profile=browser_profile)

			# Buat step callback wrapper yang dikenali Agent
			async def _step_cb(browser_state: Any, agent_output: Any, step_num: int) -> None:
				if on_step is None:
					return
				try:
					# Ambil nama aksi dari AgentOutput
					action_names: list[str] = []
					for action in agent_output.action:
						name = type(action).__name__.replace('ActionModel', '').lower()
						# Coba ambil field pertama yang ada di model sebagai nama
						d = action.model_dump(exclude_none=True)
						for k, v in d.items():
							if isinstance(v, dict) and 'url' not in k:
								name = k
								break
							elif k != 'index':
								name = k
								break
						action_names.append(name)

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

			agent = Agent(
				task=task,
				llm=self.llm,
				browser_session=browser_session,
				register_new_step_callback=_step_cb if on_step else None,
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

				return TaskResult(
					success=result.is_successful() is not False,
					output=output,
					steps=result.number_of_steps(),
					errors=errors,
					attachments=attachments,
				)
			except Exception as e:
				logger.exception(f'Agent error: {e}')
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
		**context: Any,
	) -> TaskResult:
		"""
		Hook utama: dipanggil saat ada task masuk dari channel.
		Override jika perlu pre/post processing, atau langsung pakai runner.
		"""
		self.logger.info(f'Task diterima: {task[:80]}...' if len(task) > 80 else f'Task diterima: {task}')
		return await self.runner.run(task, on_step=on_step)
