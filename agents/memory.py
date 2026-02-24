"""
agents/memory.py — MemoryAgent

Agent untuk menyimpan, mengambil, dan mengelola long-term memory.
Supervisor memanggil agent ini ketika user ingin:
- "ingat bahwa ..."
- "apa yang kamu ingat tentang ..."
- "hapus semua ingatanmu"
- "simpan preferensi ini"
"""

from __future__ import annotations

import logging

import db
from agents.base import AgentContext, AgentResult, BaseAgent

logger = logging.getLogger(__name__)


class MemoryAgent(BaseAgent):
    """
    Agent untuk manajemen long-term memory user.

    Cocok untuk:
    - Simpan preferensi / fakta penting dari user
    - Recall informasi dari task sebelumnya
    - Hapus memory
    - List semua memory tersimpan
    """

    name = 'memory'
    description = (
        'Memory management agent. Use for: saving important facts or preferences '
        '("remember that...", "save this"), recalling past information '
        '("what do you know about me?", "what did we discuss?"), '
        'listing stored memories, or deleting memories.'
    )

    async def run(self, ctx: AgentContext) -> AgentResult:
        """Handle memory operation dari task."""
        task_lower = ctx.task.lower()

        # ── Hapus memory ──────────────────────────────────────────────────
        if any(k in task_lower for k in ['hapus', 'forget', 'delete', 'clear', 'lupa', 'bersihkan']):
            try:
                count = await db.memory_delete(ctx.channel, ctx.channel_id)
                return AgentResult(
                    success=True,
                    output=f'{count} memory telah dihapus.',
                    agent_name=self.name,
                )
            except Exception as e:
                return AgentResult(
                    success=False,
                    output='Gagal menghapus memory.',
                    agent_name=self.name,
                    errors=[str(e)],
                )

        # ── Tampilkan memory ──────────────────────────────────────────────
        if any(k in task_lower for k in ['list', 'tampilkan', 'show', 'ingat apa', 'tau apa', 'apa yang']):
            try:
                memories = await db.memory_get_context(ctx.channel, ctx.channel_id, limit=10)
                if not memories:
                    return AgentResult(
                        success=True,
                        output='Belum ada memory tersimpan.',
                        agent_name=self.name,
                    )
                lines = ['Memory tersimpan:']
                for m in reversed(memories):
                    ts = m.created_at.strftime('%d/%m %H:%M') if m.created_at else '—'
                    lines.append(f'[{m.mem_type}] {ts}: {m.content[:150]}')
                return AgentResult(
                    success=True,
                    output='\n'.join(lines),
                    agent_name=self.name,
                )
            except Exception as e:
                return AgentResult(
                    success=False,
                    output='Gagal membaca memory.',
                    agent_name=self.name,
                    errors=[str(e)],
                )

        # ── Simpan memory baru ────────────────────────────────────────────
        # Ekstrak konten yang ingin disimpan dari task
        content = ctx.task
        for prefix in [
            'ingat bahwa ', 'ingat ', 'remember that ', 'remember ',
            'simpan ', 'save ', 'catat ', 'note ',
        ]:
            if task_lower.startswith(prefix):
                content = ctx.task[len(prefix):].strip()
                break

        if not content:
            return AgentResult(
                success=False,
                output='Tidak ada konten yang bisa disimpan.',
                agent_name=self.name,
            )

        try:
            await db.memory_add(
                channel=ctx.channel,
                channel_id=ctx.channel_id,
                content=content,
                mem_type='user_note',
                username=ctx.username,
                source='user_explicit',
            )
            return AgentResult(
                success=True,
                output=f'Tersimpan: "{content[:100]}"',
                agent_name=self.name,
            )
        except Exception as e:
            return AgentResult(
                success=False,
                output='Gagal menyimpan memory.',
                agent_name=self.name,
                errors=[str(e)],
            )
