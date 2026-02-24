"""
agents/chat.py — ChatAgent

Agent untuk reasoning, Q&A, ringkasan, kalkulasi, penjelasan, dan tugas
yang tidak memerlukan browser. Langsung panggil LLM tanpa browser.
"""

from __future__ import annotations

import logging
import os
import time

from openai import AsyncOpenAI

from agents.base import AgentContext, AgentResult, BaseAgent

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Kamu adalah asisten AI cerdas bernama mybrowse yang membantu pengguna.
Kamu bisa:
- Menjawab pertanyaan umum
- Meringkas teks
- Menjelaskan konsep
- Kalkulasi dan analisis
- Menulis teks, kode, atau konten
- Memberikan rekomendasi

Jawab dengan bahasa yang sama dengan pertanyaan user (Indonesia atau Inggris).
Jawaban ringkas dan padat, tapi lengkap dan akurat.
Jika ada konteks memory dari percakapan sebelumnya, gunakan untuk jawaban yang lebih personal.
"""


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

    def __init__(self, llm: object):
        super().__init__(llm)
        # Buat AsyncOpenAI client dari env yang sama dengan LLM utama
        self._client = AsyncOpenAI(
            api_key=os.environ.get('OPENAI_API_KEY', ''),
            base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        )
        self._model = os.environ.get('OPENAI_MODEL', 'gpt-4o')

    async def run(self, ctx: AgentContext) -> AgentResult:
        """Jawab task menggunakan LLM langsung (tanpa browser)."""
        start = time.time()

        # Build system message dengan memory context jika ada
        system = SYSTEM_PROMPT
        if ctx.memory_context:
            system += f'\n\n{ctx.memory_context}'

        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': ctx.task},
        ]

        if ctx.on_update:
            try:
                await ctx.on_update('[chat] Memproses pertanyaan...')
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
