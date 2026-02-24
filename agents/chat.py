"""
agents/chat.py — ChatAgent

Agent untuk reasoning, Q&A, ringkasan, kalkulasi, penjelasan, dan tugas
yang tidak memerlukan browser. Langsung panggil LLM tanpa browser.

Persona (nama AI, karakter) dan identitas pemilik diinjeksikan dari
soul.md dan identity.md melalui PersonaLoader.
"""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

from agents.base import AgentContext, AgentResult, BaseAgent
from agents.persona import get_persona

logger = logging.getLogger(__name__)


class ChatAgent(BaseAgent):
    """
    Agent untuk tugas chat/reasoning tanpa browser.

    Cocok untuk:
    - Pertanyaan umum / Q&A
    - Ringkasan teks
    - Penjelasan konsep
    - Penulisan konten
    - Kalkulasi / analisis data
    - Rekomendasi
    - Percakapan umum
    """

    name = 'chat'
    description = (
        'General reasoning and conversation agent. Use for: answering questions, '
        'summarizing text, explaining concepts, writing content, calculations, '
        'giving recommendations, general conversation. Does NOT browse the internet — '
        'use browser agent for that.'
    )

    def __init__(self, llm: object) -> None:
        super().__init__(llm)
        self._client = AsyncOpenAI(
            api_key=os.environ.get('OPENAI_API_KEY', ''),
            base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        )
        self._model = os.environ.get('OPENAI_MODEL', 'gpt-4o')

    async def run(self, ctx: AgentContext) -> AgentResult:
        """Jawab task menggunakan LLM langsung (tanpa browser)."""

        # ── Susun system prompt dari persona + memory context ─────────────
        persona = get_persona()
        system = persona.build_system_prompt(
            extra=ctx.memory_context or ''
        )

        # ── Build messages: system + history + user ───────────────────────
        messages: list[dict] = [{'role': 'system', 'content': system}]
        if ctx.history:
            messages.extend(ctx.history)
        messages.append({'role': 'user', 'content': ctx.task})

        if ctx.on_update:
            try:
                ai_name = persona.ai_name
                await ctx.on_update(f'[chat] {ai_name} memproses...')
            except Exception:
                pass

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.7,
                max_tokens=2048,
            )
            output = resp.choices[0].message.content or ''
            return AgentResult(
                success=True,
                output=output,
                agent_name=self.name,
                steps=1,
            )
        except Exception as e:
            logger.exception(f'ChatAgent error: {e}')
            return AgentResult(
                success=False,
                output='',
                agent_name=self.name,
                errors=[str(e)],
            )
