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
from typing import Any

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
class TaskResult:
	"""Hasil eksekusi task dari agent."""

	success: bool
	output: str
	steps: int
	errors: list[str]

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


class AgentRunner:
	"""Runner terpusat untuk menjalankan browser agent dari channel manapun."""

	def __init__(self, llm: BaseChatModel, config: AgentConfig | None = None):
		self.llm = llm
		self.config = config or AgentConfig()
		self._lock = asyncio.Lock()  # Satu agent berjalan pada satu waktu per runner

	async def run(self, task: str) -> TaskResult:
		"""Jalankan task menggunakan browser agent."""
		async with self._lock:
			browser_profile = BrowserProfile(
				headless=self.config.headless,
				executable_path=self.config.executable_path,
			)
			browser_session = BrowserSession(browser_profile=browser_profile)
			agent = Agent(
				task=task,
				llm=self.llm,
				browser_session=browser_session,
				**self.config.extra_agent_kwargs,
			)

			try:
				result = await agent.run(max_steps=self.config.max_steps)
				output = result.final_result() or 'Task selesai tanpa output.'
				errors = [e for e in result.errors() if e]
				return TaskResult(
					success=result.is_successful() is not False,
					output=output,
					steps=result.number_of_steps(),
					errors=errors,
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

	async def on_task(self, task: str, **context: Any) -> TaskResult:
		"""
		Hook utama: dipanggil saat ada task masuk dari channel.
		Override jika perlu pre/post processing, atau langsung pakai runner.
		"""
		self.logger.info(f'Task diterima: {task[:80]}...' if len(task) > 80 else f'Task diterima: {task}')
		return await self.runner.run(task)
