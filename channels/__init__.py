"""
channels/__init__.py — Registry Channel Modular

Untuk menambah channel baru:
1. Buat folder: channels/<nama>/
2. Buat class yang extends BaseChannel
3. Import di sini

Contoh:
    from channels.whatsapp import WhatsAppChannel  # coming soon
    from channels.discord import DiscordChannel    # coming soon
"""

from channels.base import AgentConfig, AgentRunner, BaseChannel, TaskResult
from channels.telegram import TelegramChannel

__all__ = [
	'BaseChannel',
	'AgentRunner',
	'AgentConfig',
	'TaskResult',
	'TelegramChannel',
]
