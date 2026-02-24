"""
channels/base.py — Channel base interface (Plug & Play)

Cara menambahkan channel baru:
1. Buat folder channels/<nama_channel>/
2. Buat class yang extends BaseChannel
3. Implementasikan start() dan stop()
4. Register di channels/__init__.py

Contoh:
- channels/telegram/ → TelegramChannel

Arsitektur:
  Channel → Supervisor → Agent(s) → SupervisorResult → Channel
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

from agents.base import AgentContext, BrowserConfig
from agents.supervisor import Supervisor, SupervisorResult

logger = logging.getLogger(__name__)


# Type alias: callback untuk update live dari supervisor ke channel
UpdateCallback = Callable[[str], Awaitable[None]]


class BaseChannel(ABC):
    """
    Abstract base class untuk semua channel.

    Setiap channel membuat satu Supervisor dan meneruskan pesan user ke
    supervisor.run(ctx). Hasil SupervisorResult dikirim kembali ke user.
    """

    def __init__(self, supervisor: Supervisor, **kwargs: Any):
        self.supervisor = supervisor
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def start(self) -> None:
        """Mulai channel (polling, webhook, dll)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""
        ...

    async def handle_message(
        self,
        task: str,
        channel: str,
        channel_id: str,
        username: str = 'user',
        on_update: UpdateCallback | None = None,
        memory_context: str | None = None,
    ) -> SupervisorResult:
        """
        Entry point utama: terima pesan dari channel, jalankan via Supervisor.

        Args:
            task: Pesan / perintah dari user (teks bebas, tanpa prefix /task)
            channel: Nama channel ('telegram', 'cli', 'whatsapp', ...)
            channel_id: ID unik user/chat di channel tersebut
            username: Display name user
            on_update: Async callback untuk live update ke channel
            memory_context: Pre-fetched memory (opsional, Supervisor akan fetch sendiri jika None)
        """
        self.logger.info(
            f'[{channel}:{channel_id}] {username}: {task[:80]}{"..." if len(task) > 80 else ""}'
        )

        ctx = AgentContext(
            task=task,
            channel=channel,
            channel_id=channel_id,
            username=username,
            on_update=on_update,
            memory_context=memory_context,
        )

        return await self.supervisor.run(ctx)
