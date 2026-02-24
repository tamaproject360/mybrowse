"""
db.py - Database layer untuk mybrowse menggunakan asyncpg + PostgreSQL.

Menyimpan:
- Task: setiap perintah dari user (prompt, status, output, durasi)
- StepLog: setiap langkah agent (aksi, goal, evaluasi, URL)
- Attachment: file screenshot/download yang dihasilkan agent
- Memory: long-term memory per channel/user untuk konteks lintas task

Setup:
  DATABASE_URL=postgresql://postgres:password@localhost:5432/mybrowse
"""

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


# ─── Pool singleton ──────────────────────────────────────────────────────────

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
	global _pool
	if _pool is None:
		url = os.environ.get('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/mybrowse')
		_pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
		logger.info('Database pool created')
	return _pool


async def close_pool() -> None:
	global _pool
	if _pool:
		await _pool.close()
		_pool = None
		logger.info('Database pool closed')


@asynccontextmanager
async def db() -> AsyncGenerator[asyncpg.Connection, None]:
	"""Context manager untuk koneksi DB dari pool."""
	pool = await get_pool()
	async with pool.acquire() as conn:
		yield conn


# ─── Dataclasses (return types) ──────────────────────────────────────────────

@dataclass
class TaskRecord:
	id: str
	created_at: datetime
	updated_at: datetime | None
	channel: str
	channel_id: str
	username: str | None
	prompt: str
	status: str
	output: str | None
	success: bool | None
	steps: int
	duration_ms: int | None


@dataclass
class MemoryRecord:
	id: str
	created_at: datetime
	channel: str
	channel_id: str
	content: str
	mem_type: str
	source: str | None


# ─── Task operations ─────────────────────────────────────────────────────────

async def task_create(
	channel: str,
	channel_id: str,
	prompt: str,
	username: str | None = None,
) -> str:
	"""Buat task baru, return task_id (UUID string)."""
	async with db() as conn:
		row = await conn.fetchrow(
			"""
			INSERT INTO tasks (channel, channel_id, username, prompt, status)
			VALUES ($1, $2, $3, $4, 'PENDING')
			RETURNING id
			""",
			channel, channel_id, username, prompt,
		)
		task_id = str(row['id'])
		logger.debug(f'Task created: {task_id}')
		return task_id


async def task_start(task_id: str) -> None:
	"""Tandai task sebagai RUNNING."""
	async with db() as conn:
		await conn.execute(
			"UPDATE tasks SET status='RUNNING', updated_at=NOW() WHERE id=$1",
			UUID(task_id),
		)


async def task_done(
	task_id: str,
	output: str,
	success: bool,
	steps: int,
	duration_ms: int,
) -> None:
	"""Tandai task selesai dengan hasil."""
	status = 'DONE' if success else 'FAILED'
	async with db() as conn:
		await conn.execute(
			"""
			UPDATE tasks
			SET status=$2, output=$3, success=$4, steps=$5, duration_ms=$6, updated_at=NOW()
			WHERE id=$1
			""",
			UUID(task_id), status, output, success, steps, duration_ms,
		)


async def task_cancel(task_id: str) -> None:
	"""Tandai task sebagai CANCELLED."""
	async with db() as conn:
		await conn.execute(
			"UPDATE tasks SET status='CANCELLED', updated_at=NOW() WHERE id=$1",
			UUID(task_id),
		)


async def task_get(task_id: str) -> TaskRecord | None:
	"""Ambil satu task by ID."""
	async with db() as conn:
		row = await conn.fetchrow('SELECT * FROM tasks WHERE id=$1', UUID(task_id))
		if not row:
			return None
		return TaskRecord(**{k: (str(v) if isinstance(v, UUID) else v) for k, v in dict(row).items()})


async def task_list(channel: str, channel_id: str, limit: int = 10) -> list[TaskRecord]:
	"""Ambil daftar task terbaru untuk sebuah channel."""
	async with db() as conn:
		rows = await conn.fetch(
			'SELECT * FROM tasks WHERE channel=$1 AND channel_id=$2 ORDER BY created_at DESC LIMIT $3',
			channel, channel_id, limit,
		)
		return [TaskRecord(**{k: (str(v) if isinstance(v, UUID) else v) for k, v in dict(r).items()}) for r in rows]


# ─── StepLog operations ───────────────────────────────────────────────────────

async def step_log(
	task_id: str,
	step_num: int,
	actions: list[str],
	next_goal: str = '',
	evaluation: str = '',
	url: str = '',
) -> None:
	"""Simpan log satu langkah agent."""
	async with db() as conn:
		await conn.execute(
			"""
			INSERT INTO step_logs (task_id, step_num, actions, next_goal, evaluation, url)
			VALUES ($1, $2, $3, $4, $5, $6)
			""",
			UUID(task_id), step_num, actions,
			next_goal or None, evaluation or None, url or None,
		)


# ─── Attachment operations ────────────────────────────────────────────────────

async def attachment_save(
	task_id: str,
	file_name: str,
	file_path: str,
	file_type: str = 'screenshot',
	mime_type: str | None = None,
	size_bytes: int | None = None,
) -> str:
	"""Simpan record attachment, return attachment_id."""
	async with db() as conn:
		row = await conn.fetchrow(
			"""
			INSERT INTO attachments (task_id, file_name, file_path, file_type, mime_type, size_bytes)
			VALUES ($1, $2, $3, $4, $5, $6)
			RETURNING id
			""",
			UUID(task_id), file_name, file_path, file_type, mime_type, size_bytes,
		)
		return str(row['id'])


async def attachment_mark_sent(attachment_id: str) -> None:
	"""Tandai attachment sudah dikirim ke channel."""
	async with db() as conn:
		await conn.execute(
			'UPDATE attachments SET sent_to_channel=TRUE WHERE id=$1',
			UUID(attachment_id),
		)


async def attachments_for_task(task_id: str) -> list[dict]:
	"""Ambil semua attachment untuk sebuah task."""
	async with db() as conn:
		rows = await conn.fetch('SELECT * FROM attachments WHERE task_id=$1', UUID(task_id))
		return [dict(r) for r in rows]


# ─── Memory operations ────────────────────────────────────────────────────────

async def memory_add(
	channel: str,
	channel_id: str,
	content: str,
	mem_type: str = 'general',
	username: str | None = None,
	task_id: str | None = None,
	source: str | None = None,
) -> str:
	"""Tambah memory baru, return memory_id."""
	async with db() as conn:
		row = await conn.fetchrow(
			"""
			INSERT INTO memories (channel, channel_id, username, content, mem_type, source, task_id)
			VALUES ($1, $2, $3, $4, $5, $6, $7)
			RETURNING id
			""",
			channel, channel_id, username, content, mem_type, source,
			UUID(task_id) if task_id else None,
		)
		return str(row['id'])


async def memory_get_context(
	channel: str,
	channel_id: str,
	limit: int = 10,
) -> list[MemoryRecord]:
	"""
	Ambil memory terbaru untuk konteks agent.
	Digunakan sebagai 'long-term memory' yang di-inject ke task prompt.
	"""
	async with db() as conn:
		rows = await conn.fetch(
			"""
			SELECT * FROM memories
			WHERE channel=$1 AND channel_id=$2
			ORDER BY created_at DESC
			LIMIT $3
			""",
			channel, channel_id, limit,
		)
		return [
			MemoryRecord(
				id=str(r['id']),
				created_at=r['created_at'],
				channel=r['channel'],
				channel_id=r['channel_id'],
				content=r['content'],
				mem_type=r['mem_type'],
				source=r['source'],
			)
			for r in rows
		]


async def memory_format_for_prompt(channel: str, channel_id: str, limit: int = 5) -> str:
	"""
	Format memory sebagai string untuk di-inject ke agent prompt.
	Contoh output:
	  [Memory] Pengguna lebih suka hasil dalam Bahasa Indonesia
	  [Memory] Pengguna sering mencari harga di tokopedia
	"""
	memories = await memory_get_context(channel, channel_id, limit)
	if not memories:
		return ''
	lines = ['Konteks dari percakapan sebelumnya:']
	for m in reversed(memories):  # dari yang lama ke baru
		lines.append(f'  [{m.mem_type}] {m.content}')
	return '\n'.join(lines)


async def memory_delete(channel: str, channel_id: str) -> int:
	"""Hapus semua memory untuk channel tertentu, return jumlah yang dihapus."""
	async with db() as conn:
		result = await conn.execute(
			'DELETE FROM memories WHERE channel=$1 AND channel_id=$2',
			channel, channel_id,
		)
		count = int(result.split()[-1])
		return count
