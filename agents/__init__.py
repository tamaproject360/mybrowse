"""
agents/ — Multi-agent system untuk mybrowse.

Agen yang tersedia:
- BrowserAgent  : autonomous web browsing via browser-use
- ChatAgent     : reasoning, Q&A, ringkasan, kalkulasi, dll
- MemoryAgent   : simpan & recall long-term memory dari DB
- Supervisor    : orchestrator LLM yang memilih & memanggil agen

Cara pakai:
    from agents import Supervisor
    supervisor = Supervisor(llm=llm, config=config)
    result = await supervisor.run(task, context)
"""

from agents.base import AgentContext, AgentResult, BaseAgent, BrowserConfig
from agents.supervisor import Supervisor

__all__ = [
    'Supervisor',
    'BaseAgent',
    'AgentContext',
    'AgentResult',
    'BrowserConfig',
]
