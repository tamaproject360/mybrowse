"""
agents/base.py — Shared types & base class untuk semua agent.

Setiap agent menerima AgentContext dan mengembalikan AgentResult.
Channel → Supervisor → Agent(s) → AgentResult → Channel
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


logger = logging.getLogger(__name__)


# ─── Config ──────────────────────────────────────────────────────────────────

@dataclass
class BrowserConfig:
    """Konfigurasi browser untuk BrowserAgent."""
    headless: bool = True
    executable_path: str = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
    max_steps: int = 50


# ─── Context ─────────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """
    Konteks task yang mengalir dari channel ke supervisor ke agent.

    Berisi identitas user, memory, dan callback untuk update live.
    """
    task: str                           # perintah user (raw)
    channel: str = 'cli'               # 'telegram', 'whatsapp', 'cli', ...
    channel_id: str = 'local'          # chat_id / user_id di channel
    username: str = 'user'             # display name
    memory_context: str | None = None  # long-term memory dari DB (sudah diformat)
    task_id: str | None = None         # DB task_id (diisi setelah task_create)
    on_update: Callable[[str], Awaitable[None]] | None = None
    # callback opsional untuk kirim update live (misal: "Browsing google.com...")
    history: list[dict] = field(default_factory=list)
    # percakapan sebelumnya dalam format OpenAI messages (tanpa system msg)
    # diisi oleh Supervisor sebelum memanggil agent
    extra: dict[str, Any] = field(default_factory=dict)


# ─── Result ──────────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Hasil eksekusi satu agent."""
    success: bool
    output: str                               # teks utama jawaban/hasil
    agent_name: str = 'unknown'
    steps: int = 0
    attachments: list[str] = field(default_factory=list)  # path file (screenshot dll)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── BaseAgent ───────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base untuk semua agent.

    Setiap agent mendeklarasikan `name`, `description` (untuk routing Supervisor),
    dan mengimplementasikan `run(ctx)`.
    """
    name: str = 'base'
    description: str = 'Base agent'

    def __init__(self, llm: Any):
        self.llm = llm
        self.logger = logging.getLogger(f'agent.{self.name}')

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentResult:
        """Jalankan agent dengan konteks yang diberikan."""
        ...

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} name={self.name!r}>'
