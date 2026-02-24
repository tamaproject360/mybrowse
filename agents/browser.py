"""
agents/browser.py — BrowserAgent

Menggunakan browser-use Agent untuk autonomous web browsing.
Screenshot selalu disimpan ke disk: agent diarahkan via system prompt
untuk selalu menyertakan file_name saat memanggil action screenshot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from browser_use import Agent
from browser_use.browser import BrowserProfile, BrowserSession

from agents.base import AgentContext, AgentResult, BaseAgent, BrowserConfig

logger = logging.getLogger(__name__)

# Instruksi tambahan agar agent selalu simpan screenshot ke file
_SCREENSHOT_INSTRUCTION = (
    '\n\nIMPORTANT: Whenever you take a screenshot, ALWAYS provide a file_name '
    'parameter (e.g. file_name="screenshot_step1") so the image is saved to disk '
    'and can be sent back to the user. Never call screenshot without file_name.'
)


class BrowserAgent(BaseAgent):
    """
    Agent untuk autonomous web browsing.

    Cocok untuk:
    - Cari informasi di internet
    - Buka dan interaksi dengan website
    - Ambil screenshot halaman
    - Scraping data dari web
    - Login dan isi form
    """

    name = 'browser'
    description = (
        'Autonomous web browsing agent. Use for: searching the web, opening websites, '
        'scraping data, taking screenshots, filling forms, clicking buttons, '
        'interacting with any website. Best for tasks that require navigating the internet.'
    )

    def __init__(self, llm: Any, config: BrowserConfig | None = None):
        super().__init__(llm)
        self.config = config or BrowserConfig()

    async def run(self, ctx: AgentContext) -> AgentResult:
        """Jalankan browser agent dengan task dari context."""
        # Inject memory context ke prompt jika ada
        full_task = ctx.task
        if ctx.memory_context:
            full_task = f'{ctx.memory_context}\n\n---\nTask sekarang:\n{ctx.task}'

        browser_profile = BrowserProfile(
            headless=self.config.headless,
            executable_path=self.config.executable_path,
        )
        browser_session = BrowserSession(browser_profile=browser_profile)

        # Step callback untuk live update ke channel
        async def _step_cb(browser_state: Any, agent_output: Any, step_num: int) -> None:
            if ctx.on_update is None:
                return
            try:
                actions = []
                for action in agent_output.action:
                    d = action.model_dump(exclude_none=True)
                    for k in d:
                        if k != 'index':
                            actions.append(k)
                            break
                goal = agent_output.next_goal or ''
                msg = f'[browser] step {step_num}: {", ".join(actions)}'
                if goal:
                    msg += f' → {goal[:80]}'
                await ctx.on_update(msg)
            except Exception as e:
                logger.debug(f'Step cb error: {e}')

        agent = Agent(
            task=full_task,
            llm=self.llm,
            browser_session=browser_session,
            extend_system_message=_SCREENSHOT_INSTRUCTION,
            register_new_step_callback=_step_cb,
        )

        try:
            history = await agent.run(max_steps=self.config.max_steps)
            output = history.final_result() or 'Task selesai tanpa output.'
            errors = [e for e in history.errors() if e]

            attachments: list[str] = []
            for ar in history.action_results():
                if ar and ar.attachments:
                    for p in ar.attachments:
                        if p and p not in attachments:
                            attachments.append(str(p))

            return AgentResult(
                success=history.is_successful() is not False,
                output=output,
                agent_name=self.name,
                steps=history.number_of_steps(),
                attachments=attachments,
                errors=errors,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f'BrowserAgent error: {e}')
            return AgentResult(
                success=False,
                output='',
                agent_name=self.name,
                errors=[str(e)],
            )
