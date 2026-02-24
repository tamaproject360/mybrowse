"""
channels/telegram/channel.py — Telegram Channel

Fitur:
- Kirim pesan bebas (tanpa /task prefix) → langsung ke Supervisor
- Live status update via edit-in-place saat agent bekerja
- Animasi typing loop
- Inline keyboard quick actions
- Whitelist user_id untuk keamanan
- Cancel task yang sedang berjalan
- /history, /memory, /forget commands
- Screenshot & file attachment dikirim otomatis
- Agent digunakan: browser | chat | memory (dipilih otomatis oleh Supervisor)

Setup:
1. Buat bot via @BotFather → dapatkan TELEGRAM_BOT_TOKEN
2. Dapatkan chat_id via @userinfobot atau /start bot ini
3. Tambahkan ke .env:
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_ALLOWED_USERS=123456789,987654321  # kosong = semua diizinkan

Perintah:
  /start    → menu utama
  /status   → cek status
  /cancel   → batalkan task
  /history  → riwayat task terakhir
  /memory   → tampilkan memory tersimpan
  /forget   → hapus semua memory
  /help     → bantuan
  <teks>    → langsung dikirim ke Supervisor (tidak perlu /task)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

import db
from agents.supervisor import Supervisor, SupervisorResult
from channels.base import BaseChannel

logger = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'

AGENT_ICONS = {
    'browser': '🌐',
    'chat': '💬',
    'memory': '🧠',
    'unknown': '⚡',
}


# ─── Progress bar helper ──────────────────────────────────────────────────────

def make_progress_bar(elapsed: int, width: int = 16) -> str:
    """Progress bar bergerak (tidak butuh total step yang tidak diketahui)."""
    pos = (elapsed // 2) % (width + 1)
    bar = '░' * pos + '█' + '░' * (width - pos)
    return f'[{bar[:width]}]'


# ─── Session state per-chat ───────────────────────────────────────────────────

@dataclass
class ChatSession:
    """State untuk setiap chat aktif."""
    chat_id: int
    task_coroutine: asyncio.Task | None = None
    progress_msg_id: int | None = None
    start_time: float = field(default_factory=time.time)
    last_update_text: str = ''     # teks terakhir yang diedit (untuk dedup)
    agent_used: str = ''


# ─── TelegramChannel ─────────────────────────────────────────────────────────

class TelegramChannel(BaseChannel):
    """
    Channel Telegram.

    Pesan biasa (bukan command) langsung dikirim ke Supervisor.
    Supervisor memilih agent yang tepat: browser / chat / memory.
    """

    def __init__(
        self,
        supervisor: Supervisor,
        token: str,
        allowed_users: list[int] | None = None,
        poll_timeout: int = 30,
        **kwargs: Any,
    ):
        super().__init__(supervisor, **kwargs)
        self.token = token
        self.allowed_users = set(allowed_users) if allowed_users else None
        self.poll_timeout = poll_timeout
        self._offset = 0
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._chats: dict[int, ChatSession] = {}

    # ─── Telegram API helpers ─────────────────────────────────────────

    def _url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self.token, method=method)

    async def _api(self, method: str, **params: Any) -> dict:
        assert self._session is not None
        try:
            async with self._session.post(self._url(method), json=params) as resp:
                data = await resp.json()
                if not data.get('ok'):
                    desc = data.get('description', 'unknown error')
                    if 'message is not modified' not in desc:
                        logger.warning(f'Telegram [{method}]: {desc}')
                    return {}
                return data.get('result', {})
        except Exception as e:
            logger.error(f'Telegram API [{method}]: {e}')
            return {}

    async def _send(
        self,
        chat_id: int,
        text: str,
        reply_to: int | None = None,
        keyboard: list[list[dict]] | None = None,
        parse_mode: str = 'HTML',
    ) -> int | None:
        """Kirim pesan, return message_id."""
        max_len = 4096
        msg_id = None
        chunks = [text[i: i + max_len] for i in range(0, max(len(text), 1), max_len)]
        for i, chunk in enumerate(chunks):
            params: dict[str, Any] = {
                'chat_id': chat_id,
                'text': chunk,
                'parse_mode': parse_mode,
            }
            if reply_to and i == 0:
                params['reply_to_message_id'] = reply_to
            if keyboard and i == len(chunks) - 1:
                params['reply_markup'] = {'inline_keyboard': keyboard}
            result = await self._api('sendMessage', **params)
            if i == 0:
                msg_id = result.get('message_id')
        return msg_id

    async def _edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: list[list[dict]] | None = None,
    ) -> bool:
        params: dict[str, Any] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text[:4096],
            'parse_mode': 'HTML',
        }
        if keyboard is not None:
            params['reply_markup'] = {'inline_keyboard': keyboard}
        return bool(await self._api('editMessageText', **params))

    async def _answer_callback(self, cq_id: str, text: str = '') -> None:
        await self._api('answerCallbackQuery', callback_query_id=cq_id, text=text)

    async def _typing(self, chat_id: int) -> None:
        await self._api('sendChatAction', chat_id=chat_id, action='typing')

    async def _send_photo(
        self,
        chat_id: int,
        photo_path: str,
        caption: str = '',
        keyboard: list[list[dict]] | None = None,
    ) -> int | None:
        assert self._session is not None
        path = Path(photo_path)
        if not path.exists():
            logger.warning(f'File tidak ditemukan: {photo_path}')
            return None
        file_size = path.stat().st_size
        use_document = file_size > 10 * 1024 * 1024
        try:
            form = aiohttp.FormData()
            form.add_field('chat_id', str(chat_id))
            if caption:
                form.add_field('caption', caption[:1024])
                form.add_field('parse_mode', 'HTML')
            if keyboard:
                form.add_field('reply_markup', json.dumps({'inline_keyboard': keyboard}))
            with open(photo_path, 'rb') as f:
                field_name = 'document' if use_document else 'photo'
                ext = path.suffix.lower()
                mime = 'image/png' if ext == '.png' else 'image/jpeg'
                form.add_field(field_name, f, filename=path.name, content_type=mime)
                method = 'sendDocument' if use_document else 'sendPhoto'
                async with self._session.post(self._url(method), data=form) as resp:
                    data = await resp.json()
                    if data.get('ok'):
                        return data.get('result', {}).get('message_id')
                    logger.error(f'Gagal kirim foto: {data.get("description")}')
                    return None
        except Exception as e:
            logger.error(f'Error kirim foto: {e}')
            return None

    async def _send_document(self, chat_id: int, file_path: str, caption: str = '') -> None:
        assert self._session is not None
        path = Path(file_path)
        if not path.exists():
            return
        try:
            form = aiohttp.FormData()
            form.add_field('chat_id', str(chat_id))
            if caption:
                form.add_field('caption', caption[:1024])
            with open(file_path, 'rb') as f:
                form.add_field('document', f, filename=path.name)
                async with self._session.post(self._url('sendDocument'), data=form) as resp:
                    data = await resp.json()
                    if not data.get('ok'):
                        logger.error(f'Gagal kirim document: {data.get("description")}')
        except Exception as e:
            logger.error(f'Error kirim document: {e}')

    # ─── Keyboard layouts ─────────────────────────────────────────────

    def _main_keyboard(self) -> list[list[dict]]:
        return [
            [
                {'text': '📊 Status', 'callback_data': 'cmd:status'},
                {'text': '❓ Help', 'callback_data': 'cmd:help'},
            ],
            [
                {'text': '🚫 Cancel', 'callback_data': 'cmd:cancel'},
                {'text': '🧠 Memory', 'callback_data': 'cmd:memory'},
            ],
        ]

    def _running_keyboard(self) -> list[list[dict]]:
        return [[{'text': '🚫 Cancel Task', 'callback_data': 'cmd:cancel'}]]

    def _done_keyboard(self) -> list[list[dict]]:
        return [
            [
                {'text': '📊 Status', 'callback_data': 'cmd:status'},
                {'text': '🧠 Memory', 'callback_data': 'cmd:memory'},
            ],
        ]

    # ─── Session helpers ──────────────────────────────────────────────

    def _get_session(self, chat_id: int) -> ChatSession:
        if chat_id not in self._chats:
            self._chats[chat_id] = ChatSession(chat_id=chat_id)
        return self._chats[chat_id]

    def _is_busy(self, chat_id: int) -> bool:
        s = self._chats.get(chat_id)
        return s is not None and s.task_coroutine is not None and not s.task_coroutine.done()

    def _is_allowed(self, chat_id: int) -> bool:
        return self.allowed_users is None or chat_id in self.allowed_users

    # ─── Progress message ─────────────────────────────────────────────

    def _build_progress(self, task: str, session: ChatSession, status_line: str = '') -> str:
        elapsed = int(time.time() - session.start_time)
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f'{mins}m {secs}s' if mins else f'{secs}s'
        bar = make_progress_bar(elapsed)
        task_preview = task[:60] + '...' if len(task) > 60 else task
        agent_icon = AGENT_ICONS.get(session.agent_used, '⚡')
        lines = [
            f'<b>{agent_icon} Agent Berjalan</b>',
            f'<code>{task_preview}</code>',
            '',
            f'<b>Waktu:</b> {elapsed_str} {bar}',
        ]
        if status_line:
            lines.append(f'<b>Status:</b> {status_line}')
        return '\n'.join(lines)

    # ─── Command handlers ─────────────────────────────────────────────

    async def _cmd_start(self, chat_id: int, msg_id: int, username: str) -> None:
        text = (
            f'Halo <b>{username}</b>!\n\n'
            f'Saya <b>mybrowse</b> — AI agent yang bisa browsing internet, menjawab pertanyaan, '
            f'dan mengingat percakapan kita.\n\n'
            f'<b>Cara pakai:</b>\n'
            f'Cukup kirim pesan biasa — saya akan otomatis pilih cara terbaik:\n'
            f'  🌐 Browsing web jika perlu internet\n'
            f'  💬 Jawab langsung jika bisa\n'
            f'  🧠 Ingat preferensimu lintas sesi\n\n'
            f'<b>Contoh:</b>\n'
            f'<code>cari harga iPhone 16 di tokopedia</code>\n'
            f'<code>jelaskan apa itu transformer dalam AI</code>\n'
            f'<code>ingat bahwa saya suka hasil dalam bahasa Indonesia</code>\n\n'
            f'<b>Perintah:</b>\n'
            f'  /status — status bot\n'
            f'  /history — riwayat task\n'
            f'  /memory — memory tersimpan\n'
            f'  /forget — hapus memory\n'
            f'  /help — bantuan lengkap'
        )
        await self._send(chat_id, text, reply_to=msg_id, keyboard=self._main_keyboard())

    async def _cmd_help(self, chat_id: int, msg_id: int) -> None:
        text = (
            f'<b>mybrowse — Panduan</b>\n\n'
            f'<b>Cukup kirim pesan biasa</b>, tidak perlu prefix /task.\n'
            f'AI akan otomatis memilih:\n'
            f'  🌐 <b>Browser</b> — untuk browsing, cari info online, screenshot\n'
            f'  💬 <b>Chat</b> — untuk Q&amp;A, penjelasan, penulisan, kalkulasi\n'
            f'  🧠 <b>Memory</b> — untuk simpan/recall preferensi\n\n'
            f'<b>Contoh pesan:</b>\n'
            f'<code>buka tokopedia dan cari laptop gaming</code>\n'
            f'<code>berapa jarak bumi ke bulan?</code>\n'
            f'<code>tulis email permohonan cuti</code>\n'
            f'<code>ingat bahwa saya tinggal di Jakarta</code>\n'
            f'<code>kamu ingat apa tentang saya?</code>\n\n'
            f'<b>Perintah bot:</b>\n'
            f'  /cancel — batalkan task berjalan\n'
            f'  /status — cek status\n'
            f'  /history — 5 task terakhir\n'
            f'  /memory — lihat memory\n'
            f'  /forget — hapus semua memory'
        )
        await self._send(chat_id, text, reply_to=msg_id, keyboard=self._main_keyboard())

    async def _cmd_status(self, chat_id: int, msg_id: int) -> None:
        busy = self._is_busy(chat_id)
        if busy:
            session = self._get_session(chat_id)
            elapsed = int(time.time() - session.start_time)
            mins, secs = divmod(elapsed, 60)
            elapsed_str = f'{mins}m {secs}s' if mins else f'{secs}s'
            icon = AGENT_ICONS.get(session.agent_used, '⚡')
            text = (
                f'<b>📊 Status</b>\n\n'
                f'Status: <b>🔄 Task sedang berjalan</b>\n'
                f'Agent: <b>{icon} {session.agent_used or "..."}</b>\n'
                f'Berjalan: <b>{elapsed_str}</b>'
            )
        else:
            text = (
                f'<b>📊 Status</b>\n\n'
                f'Status: <b>✅ Siap</b>\n'
                f'Kirim pesan apa saja untuk memulai.'
            )
        await self._send(chat_id, text, reply_to=msg_id, keyboard=self._main_keyboard())

    async def _cmd_cancel(self, chat_id: int, msg_id: int) -> None:
        session = self._get_session(chat_id)
        if session.task_coroutine and not session.task_coroutine.done():
            session.task_coroutine.cancel()
            await self._send(chat_id, '🚫 Membatalkan task...', reply_to=msg_id)
        else:
            await self._send(
                chat_id, 'Tidak ada task yang sedang berjalan.',
                reply_to=msg_id, keyboard=self._main_keyboard(),
            )

    async def _cmd_history(self, chat_id: int, msg_id: int) -> None:
        try:
            records = await db.task_list('telegram', str(chat_id), limit=5)
        except Exception as e:
            await self._send(chat_id, f'❌ Gagal: <code>{e}</code>', reply_to=msg_id)
            return
        if not records:
            await self._send(chat_id, 'Belum ada riwayat.', reply_to=msg_id, keyboard=self._main_keyboard())
            return
        icons = {'DONE': '✅', 'FAILED': '❌', 'CANCELLED': '🚫', 'RUNNING': '🔄', 'PENDING': '⏳'}
        lines = ['<b>📋 Riwayat Task</b>\n']
        for i, r in enumerate(records, 1):
            icon = icons.get(r.status, '•')
            prompt = r.prompt[:60] + '...' if len(r.prompt) > 60 else r.prompt
            dur = f'{r.duration_ms // 1000}s' if r.duration_ms else '—'
            lines.append(f'{i}. {icon} <code>{prompt}</code>\n   {r.status} | {dur}')
        await self._send(chat_id, '\n\n'.join(lines), reply_to=msg_id, keyboard=self._main_keyboard())

    async def _cmd_memory(self, chat_id: int, msg_id: int) -> None:
        try:
            memories = await db.memory_get_context('telegram', str(chat_id), limit=10)
        except Exception as e:
            await self._send(chat_id, f'❌ Gagal: <code>{e}</code>', reply_to=msg_id)
            return
        if not memories:
            await self._send(
                chat_id,
                'Belum ada memory.\n\nCoba: <code>ingat bahwa saya suka ringkasan singkat</code>',
                reply_to=msg_id, keyboard=self._main_keyboard(),
            )
            return
        lines = ['<b>🧠 Memory Tersimpan</b>\n']
        for m in reversed(memories):
            ts = m.created_at.strftime('%d/%m %H:%M') if m.created_at else '—'
            lines.append(f'[{m.mem_type}] <i>{ts}</i>\n{m.content[:120]}')
        lines.append('\n/forget untuk hapus semua.')
        await self._send(chat_id, '\n\n'.join(lines), reply_to=msg_id, keyboard=self._main_keyboard())

    async def _cmd_forget(self, chat_id: int, msg_id: int) -> None:
        try:
            count = await db.memory_delete('telegram', str(chat_id))
            await self._send(chat_id, f'🗑 {count} memory dihapus.', reply_to=msg_id, keyboard=self._main_keyboard())
        except Exception as e:
            await self._send(chat_id, f'❌ Gagal: <code>{e}</code>', reply_to=msg_id)

    # ─── Main message handler ─────────────────────────────────────────

    async def _handle_message(self, chat_id: int, msg_id: int, text: str, username: str) -> None:
        """Kirim pesan user ke Supervisor dan kirim hasilnya."""

        if self._is_busy(chat_id):
            await self._send(
                chat_id,
                'Ada task yang sedang berjalan. Gunakan /cancel untuk membatalkan.',
                reply_to=msg_id,
                keyboard=self._running_keyboard(),
            )
            return

        session = self._get_session(chat_id)
        session.start_time = time.time()
        session.agent_used = ''
        session.progress_msg_id = None
        session.last_update_text = ''

        # Kirim pesan progress awal
        init_text = (
            f'<b>⏳ Memproses...</b>\n'
            f'<code>{text[:60]}{"..." if len(text) > 60 else ""}</code>\n\n'
            f'Menganalisis task...'
        )
        prog_msg_id = await self._send(chat_id, init_text, reply_to=msg_id, keyboard=self._running_keyboard())
        session.progress_msg_id = prog_msg_id

        # Throttled edit: update pesan progress max setiap 2 detik
        _last_edit = [time.time() - 10]  # allow immediate first edit

        async def on_update(status: str) -> None:
            """Live update callback dari supervisor/agent."""
            # Deteksi agent dari status string
            if '[browser]' in status:
                session.agent_used = 'browser'
            elif '[chat]' in status:
                session.agent_used = 'chat'
            elif '[memory]' in status:
                session.agent_used = 'memory'
            # Ekstrak nama agent dari "Menggunakan X agent..."
            for name in ('browser', 'chat', 'memory'):
                if name in status.lower():
                    session.agent_used = name

            now = time.time()
            if now - _last_edit[0] < 2.0:
                return
            _last_edit[0] = now

            if session.progress_msg_id:
                new_text = self._build_progress(text, session, status_line=status)
                if new_text != session.last_update_text:
                    session.last_update_text = new_text
                    await self._edit(chat_id, session.progress_msg_id, new_text, keyboard=self._running_keyboard())

        async def run_and_reply() -> None:
            typing_stop = asyncio.Event()

            async def typing_loop() -> None:
                while not typing_stop.is_set():
                    await self._typing(chat_id)
                    try:
                        await asyncio.wait_for(typing_stop.wait(), timeout=4.0)
                    except asyncio.TimeoutError:
                        pass

            typing_task = asyncio.create_task(typing_loop())

            try:
                result: SupervisorResult = await self.handle_message(
                    task=text,
                    channel='telegram',
                    channel_id=str(chat_id),
                    username=username,
                    on_update=on_update,
                )
                typing_stop.set()
                typing_task.cancel()

                # Update progress jadi "selesai"
                if session.progress_msg_id:
                    elapsed = int(time.time() - session.start_time)
                    mins, secs = divmod(elapsed, 60)
                    elapsed_str = f'{mins}m {secs}s' if mins else f'{secs}s'
                    agent_icon = AGENT_ICONS.get(result.agent_used, '⚡')
                    done_text = (
                        f'<b>✅ Selesai</b>\n'
                        f'<code>{text[:60]}{"..." if len(text) > 60 else ""}</code>\n\n'
                        f'Agent: {agent_icon} <b>{result.agent_used}</b>\n'
                        f'Waktu: <b>{elapsed_str}</b>'
                    )
                    await self._edit(chat_id, session.progress_msg_id, done_text, keyboard=[])

                # Kirim hasil ke user
                status_icon = '✅' if result.success else '❌'
                output = result.output or '(tidak ada output)'
                # Potong jika terlalu panjang untuk Telegram
                if len(output) > 3800:
                    output = output[:3800] + '\n\n<i>[output dipotong]</i>'
                result_text = f'{status_icon} <b>Hasil</b>\n\n{output}'
                await self._send(chat_id, result_text, keyboard=self._done_keyboard())

                # Kirim screenshot/attachment jika ada
                if result.attachments:
                    img_paths = [p for p in result.attachments if p.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
                    other_paths = [p for p in result.attachments if p not in img_paths]
                    if img_paths:
                        cap = f'📸 <b>Screenshot</b> ({len(img_paths)} gambar)'
                        for i, path in enumerate(img_paths):
                            await self._send_photo(chat_id, path, caption=cap if i == 0 else '')
                            if len(img_paths) > 1:
                                await asyncio.sleep(0.3)
                    for path in other_paths:
                        if Path(path).exists():
                            await self._send_document(chat_id, path)

            except asyncio.CancelledError:
                typing_stop.set()
                typing_task.cancel()
                if session.progress_msg_id:
                    elapsed = int(time.time() - session.start_time)
                    cancel_text = (
                        f'<b>🚫 Dibatalkan</b>\n'
                        f'<code>{text[:60]}</code>\n'
                        f'Berjalan {elapsed}s sebelum dibatalkan.'
                    )
                    await self._edit(chat_id, session.progress_msg_id, cancel_text, keyboard=[])
                await self._send(chat_id, 'Task dibatalkan.', keyboard=self._main_keyboard())

            except Exception as e:
                typing_stop.set()
                typing_task.cancel()
                self.logger.exception(f'Error: {e}')
                if session.progress_msg_id:
                    err_text = f'<b>❌ Error</b>\n<code>{str(e)[:200]}</code>'
                    await self._edit(chat_id, session.progress_msg_id, err_text, keyboard=[])
                await self._send(chat_id, f'❌ <code>{str(e)[:200]}</code>', keyboard=self._main_keyboard())

            finally:
                session.task_coroutine = None

        coro = asyncio.create_task(run_and_reply())
        session.task_coroutine = coro

    # ─── Update dispatcher ────────────────────────────────────────────

    async def _handle_update(self, update: dict) -> None:
        # ── Callback query (inline keyboard) ─────────────────────────
        if 'callback_query' in update:
            cq = update['callback_query']
            cq_id = cq['id']
            chat_id = cq['message']['chat']['id']
            msg_id = cq['message']['message_id']
            data = cq.get('data', '')
            username = cq.get('from', {}).get('username', str(chat_id))
            await self._answer_callback(cq_id)
            if not self._is_allowed(chat_id):
                return
            if data == 'cmd:status':
                await self._cmd_status(chat_id, msg_id)
            elif data == 'cmd:help':
                await self._cmd_help(chat_id, msg_id)
            elif data == 'cmd:cancel':
                await self._cmd_cancel(chat_id, msg_id)
            elif data == 'cmd:memory':
                await self._cmd_memory(chat_id, msg_id)
            return

        # ── Regular message ───────────────────────────────────────────
        message = update.get('message') or update.get('edited_message')
        if not message:
            return

        chat_id: int = message['chat']['id']
        msg_id: int = message['message_id']
        text: str = message.get('text', '').strip()
        username: str = message.get('from', {}).get('username', str(chat_id))

        if not text:
            return

        if not self._is_allowed(chat_id):
            await self._send(chat_id, '⛔ Akses ditolak.', reply_to=msg_id)
            logger.warning(f'Akses ditolak: {username} ({chat_id})')
            return

        # Parse command (pisahkan @botname)
        cmd = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''

        if cmd == '/start':
            await self._cmd_start(chat_id, msg_id, username)
        elif cmd == '/help':
            await self._cmd_help(chat_id, msg_id)
        elif cmd == '/status':
            await self._cmd_status(chat_id, msg_id)
        elif cmd == '/cancel':
            await self._cmd_cancel(chat_id, msg_id)
        elif cmd == '/history':
            await self._cmd_history(chat_id, msg_id)
        elif cmd == '/memory':
            await self._cmd_memory(chat_id, msg_id)
        elif cmd == '/forget':
            await self._cmd_forget(chat_id, msg_id)
        elif cmd in ('/task',):
            # Backward compat: strip /task prefix dan kirim sebagai pesan biasa
            task_text = text[len(cmd):].strip()
            if '@' in text.split()[0]:
                parts = text.split(' ', 1)
                task_text = parts[1].strip() if len(parts) > 1 else ''
            if task_text:
                await self._handle_message(chat_id, msg_id, task_text, username)
            else:
                await self._send(
                    chat_id,
                    'Contoh: <code>/task cari harga laptop di tokopedia</code>\n'
                    'Atau cukup kirim pesan biasa tanpa /task',
                    reply_to=msg_id,
                    keyboard=self._main_keyboard(),
                )
        elif not text.startswith('/'):
            # Pesan biasa → langsung ke Supervisor
            await self._handle_message(chat_id, msg_id, text, username)
        # Perintah / yang tidak dikenal → abaikan saja

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Mulai long polling Telegram."""
        self._session = aiohttp.ClientSession()
        self._running = True

        me = await self._api('getMe')
        if not me:
            await self._session.close()
            raise RuntimeError('Gagal terhubung ke Telegram. Periksa TELEGRAM_BOT_TOKEN.')

        bot_name = me.get('username', 'unknown')
        self.logger.info(f'Telegram bot @{bot_name} terhubung. Polling...')

        # Update bot commands di Telegram
        await self._api(
            'setMyCommands',
            commands=[
                {'command': 'start', 'description': 'Menu utama'},
                {'command': 'status', 'description': 'Cek status bot'},
                {'command': 'cancel', 'description': 'Batalkan task berjalan'},
                {'command': 'history', 'description': 'Riwayat 5 task terakhir'},
                {'command': 'memory', 'description': 'Tampilkan memory tersimpan'},
                {'command': 'forget', 'description': 'Hapus semua memory'},
                {'command': 'help', 'description': 'Bantuan'},
            ],
        )

        # Flush pending updates lama
        try:
            await self._api('getUpdates', offset=-1, timeout=1)
        except Exception:
            pass

        # Long polling loop
        while self._running:
            try:
                updates = await self._api(
                    'getUpdates',
                    offset=self._offset,
                    timeout=self.poll_timeout,
                    allowed_updates=['message', 'edited_message', 'callback_query'],
                )
                if isinstance(updates, list):
                    for upd in updates:
                        self._offset = upd['update_id'] + 1
                        asyncio.create_task(self._handle_update(upd))
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    self.logger.error(f'Polling error: {e}')
                    await asyncio.sleep(5)

        await self._cleanup()

    async def stop(self) -> None:
        self.logger.info('Menghentikan Telegram bot...')
        self._running = False
        for session in self._chats.values():
            if session.task_coroutine and not session.task_coroutine.done():
                session.task_coroutine.cancel()
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
