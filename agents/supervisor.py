"""
agents/supervisor.py — Supervisor (Orchestrator)

Supervisor adalah "otak" dari sistem multi-agent mybrowse.
Ia menerima task dari channel, menentukan agent mana yang paling tepat,
menjalankannya, dan mengembalikan hasil yang sudah digabungkan.

Routing logic:
1. LLM diminta memilih agent yang paling tepat berdasarkan deskripsi agent
2. Agent dipanggil dengan konteks lengkap
3. Hasil dikembalikan ke channel

Flow lengkap:
  Channel
    → Supervisor.run(ctx)
        → [DB] task_create, memory_get_context
        → LLM routing: pilih agent
        → on_update("Routing ke agent X...")
        → Agent.run(ctx)
        → [DB] task_done, step_log, attachment_save, memory_add
        → SupervisorResult (gabungan semua AgentResult)
    → Channel kirim hasil ke user
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

import db
from agents.base import AgentContext, AgentResult, BaseAgent, BrowserConfig
from agents.browser import BrowserAgent
from agents.chat import ChatAgent
from agents.memory import MemoryAgent

logger = logging.getLogger(__name__)


# ─── SupervisorResult ─────────────────────────────────────────────────────────

@dataclass
class SupervisorResult:
    """Hasil akhir yang dikembalikan ke channel."""
    success: bool
    output: str
    agent_used: str                          # nama agent yang dipakai
    steps: int = 0
    attachments: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def format(self) -> str:
        status = 'Selesai' if self.success else 'Gagal'
        lines = [
            f'Status: {status}',
            f'Agent: {self.agent_used}',
            f'Langkah: {self.steps}',
            '',
            'Hasil:',
            self.output,
        ]
        if self.errors and any(e for e in self.errors if e):
            lines += ['', 'Error:', *[f'- {e}' for e in self.errors if e]]
        return '\n'.join(lines)


# ─── Supervisor ───────────────────────────────────────────────────────────────

ROUTER_SYSTEM = """You are a task router for a multi-agent AI system called mybrowse.
Your job is to select the BEST agent for the user's task.

Available agents:
{agent_descriptions}

Rules:
- ALWAYS pick exactly ONE agent
- If the task requires browsing the internet, opening websites, or real-time web data → browser
- If the task is a question, explanation, writing, calculation, summary → chat
- If the task is about saving/recalling/deleting memories → memory
- When in doubt between browser and chat, prefer browser for anything web-related

Respond with ONLY valid JSON in this exact format:
{{"agent": "<agent_name>", "reason": "<one sentence why>"}}
"""


class Supervisor:
    """
    LLM Orchestrator yang mengelola semua agent dan routing task.

    Dapat diinstansiasi sekali dan digunakan oleh semua channel.
    Thread-safe: menggunakan asyncio.Lock() per eksekusi browser agar
    tidak ada dua browser berjalan bersamaan.

    Conversation history disimpan in-memory per (channel, channel_id),
    capped di MAX_HISTORY_MESSAGES pesan. Gunakan clear_history() untuk reset.
    """

    MAX_HISTORY_MESSAGES = 20  # simpan max 20 pesan (10 turn) per sesi

    def __init__(self, llm: Any, config: BrowserConfig | None = None):
        self.llm = llm
        self.config = config or BrowserConfig()
        self._browser_lock = asyncio.Lock()
        self._client = AsyncOpenAI(
            api_key=os.environ.get('OPENAI_API_KEY', ''),
            base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        )
        self._model = os.environ.get('OPENAI_MODEL', 'gpt-4o')

        # Conversation history: {f"{channel}:{channel_id}": [msg, ...]}
        self._histories: dict[str, list[dict]] = {}

        # Registry agen — mudah ditambah/dihapus
        self._agents: dict[str, BaseAgent] = {
            'browser': BrowserAgent(llm=self.llm, config=self.config),
            'chat': ChatAgent(llm=self.llm),
            'memory': MemoryAgent(llm=self.llm),
        }

    def register_agent(self, agent: BaseAgent) -> None:
        """Tambahkan agent baru ke registry. Plug & play."""
        self._agents[agent.name] = agent
        logger.info(f'Agent registered: {agent.name}')

    def clear_history(self, channel: str, channel_id: str) -> int:
        """Hapus conversation history untuk satu sesi. Return jumlah pesan yang dihapus."""
        key = f'{channel}:{channel_id}'
        hist = self._histories.pop(key, [])
        logger.info(f'History cleared for {key}: {len(hist)} messages removed')
        return len(hist)

    def _get_history(self, channel: str, channel_id: str) -> list[dict]:
        """Ambil history untuk satu sesi (buat jika belum ada)."""
        return self._histories.setdefault(f'{channel}:{channel_id}', [])

    def _append_history(self, channel: str, channel_id: str, user_msg: str, assistant_msg: str) -> None:
        """Tambahkan turn ke history, buang yang paling lama jika melebihi cap."""
        hist = self._get_history(channel, channel_id)
        hist.append({'role': 'user', 'content': user_msg})
        hist.append({'role': 'assistant', 'content': assistant_msg})
        # Potong dari depan jika melebihi batas
        if len(hist) > self.MAX_HISTORY_MESSAGES:
            excess = len(hist) - self.MAX_HISTORY_MESSAGES
            del hist[:excess]

    # ─── Routing ──────────────────────────────────────────────────────────

    async def _route(self, task: str) -> str:
        """
        Tanya LLM agent mana yang paling tepat untuk task ini.
        Fallback ke 'chat' jika routing gagal.
        """
        descriptions = '\n'.join(
            f'- {name}: {agent.description}'
            for name, agent in self._agents.items()
        )
        system = ROUTER_SYSTEM.format(agent_descriptions=descriptions)

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': task},
                ],
                temperature=0.0,
                max_tokens=100,
                response_format={'type': 'json_object'},
            )
            raw = resp.choices[0].message.content or '{}'
            data = json.loads(raw)
            agent_name = data.get('agent', 'chat')
            reason = data.get('reason', '')
            if agent_name not in self._agents:
                logger.warning(f'Router returned unknown agent "{agent_name}", fallback to chat')
                agent_name = 'chat'
            logger.info(f'Routing → {agent_name}: {reason}')
            return agent_name
        except Exception as e:
            logger.warning(f'Router error (fallback to chat): {e}')
            return 'chat'

    # ─── Main run ─────────────────────────────────────────────────────────

    async def run(self, ctx: AgentContext) -> SupervisorResult:
        """
        Jalankan task end-to-end:
        1. Fetch memory context dari DB
        2. Route ke agent yang tepat
        3. Eksekusi agent
        4. Simpan hasil ke DB
        5. Return SupervisorResult
        """
        start_ts = time.time()

        # ── 1. Fetch memory context ────────────────────────────────────
        if not ctx.memory_context:
            try:
                ctx.memory_context = await db.memory_format_for_prompt(
                    ctx.channel, ctx.channel_id, limit=5
                ) or None
            except Exception as e:
                logger.debug(f'Gagal fetch memory: {e}')

        # ── 1b. Inject conversation history ───────────────────────────
        if not ctx.history:
            ctx.history = list(self._get_history(ctx.channel, ctx.channel_id))

        # ── 2. Create DB task record ───────────────────────────────────
        task_id: str | None = None
        try:
            task_id = await db.task_create(
                channel=ctx.channel,
                channel_id=ctx.channel_id,
                prompt=ctx.task,
                username=ctx.username,
            )
            ctx.task_id = task_id
        except Exception as e:
            logger.warning(f'DB task_create gagal: {e}')

        # ── 3. Route ───────────────────────────────────────────────────
        if ctx.on_update:
            try:
                await ctx.on_update('Menganalisis task...')
            except Exception:
                pass

        agent_name = await self._route(ctx.task)
        agent = self._agents[agent_name]

        if ctx.on_update:
            try:
                icons = {'browser': '🌐', 'chat': '💬', 'memory': '🧠'}
                icon = icons.get(agent_name, '⚡')
                await ctx.on_update(f'{icon} Menggunakan {agent_name} agent...')
            except Exception:
                pass

        # ── 4. Execute agent ───────────────────────────────────────────
        # Browser agent butuh lock agar hanya satu instance jalan
        if agent_name == 'browser':
            async with self._browser_lock:
                if task_id:
                    try:
                        await db.task_start(task_id)
                    except Exception:
                        pass
                result: AgentResult = await agent.run(ctx)
        else:
            if task_id:
                try:
                    await db.task_start(task_id)
                except Exception:
                    pass
            result = await agent.run(ctx)

        duration_ms = int((time.time() - start_ts) * 1000)

        # ── 5. Simpan ke DB ────────────────────────────────────────────
        if task_id:
            try:
                await db.task_done(
                    task_id=task_id,
                    output=result.output,
                    success=result.success,
                    steps=result.steps,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                logger.warning(f'DB task_done gagal: {e}')

            # Log step summary
            try:
                await db.step_log(
                    task_id=task_id,
                    step_num=1,
                    actions=[agent_name],
                    next_goal='',
                    evaluation='done' if result.success else 'failed',
                    url='',
                )
            except Exception:
                pass

            # Simpan attachments
            for path in result.attachments:
                try:
                    from pathlib import Path
                    p = Path(path)
                    ext = p.suffix.lower()
                    ftype = 'screenshot' if ext in ('.png', '.jpg', '.jpeg', '.webp') else 'file'
                    mime = 'image/png' if ext == '.png' else ('image/jpeg' if ext in ('.jpg', '.jpeg') else None)
                    await db.attachment_save(
                        task_id=task_id,
                        file_name=p.name,
                        file_path=path,
                        file_type=ftype,
                        mime_type=mime,
                        size_bytes=p.stat().st_size if p.exists() else None,
                    )
                except Exception:
                    pass

        # Auto-save hasil ke memory jika sukses & ada output bermakna
        if result.success and result.output and len(result.output) > 20:
            try:
                summary = result.output[:400]
                await db.memory_add(
                    channel=ctx.channel,
                    channel_id=ctx.channel_id,
                    content=f'Task: {ctx.task[:100]}\nHasil: {summary}',
                    mem_type='task_result',
                    username=ctx.username,
                    task_id=task_id,
                    source=agent_name,
                )
            except Exception as e:
                logger.debug(f'Auto-memory save gagal: {e}')

        # ── 6. Update conversation history ─────────────────────────────
        if result.output:
            try:
                self._append_history(
                    ctx.channel, ctx.channel_id,
                    user_msg=ctx.task,
                    assistant_msg=result.output[:1000],  # cap per-message
                )
            except Exception as e:
                logger.debug(f'History append gagal: {e}')

        return SupervisorResult(
            success=result.success,
            output=result.output,
            agent_used=agent_name,
            steps=result.steps,
            attachments=result.attachments,
            errors=result.errors,
        )
