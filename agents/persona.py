"""
agents/persona.py — PersonaLoader

Membaca soul.md (karakter AI) dan identity.md (profil pemilik),
menyusunnya menjadi system prompt yang diinjeksikan ke semua agent.

Fitur:
- Singleton: cukup satu instance untuk seluruh proses
- Cache: file hanya dibaca ulang jika mtime berubah (hot-reload tanpa restart)
- Graceful fallback: jika file tidak ada, gunakan default minimal
- Parse nama pemilik dari identity.md untuk sapaan personal
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Path default — bisa di-override lewat env
_DEFAULT_SOUL     = Path(__file__).parent.parent / 'soul.md'
_DEFAULT_IDENTITY = Path(__file__).parent.parent / 'identity.md'


# ─── PersonaData ─────────────────────────────────────────────────────────────

@dataclass
class PersonaData:
    """Hasil parse dari soul.md + identity.md."""
    soul_text: str       = ''   # isi lengkap soul.md
    identity_text: str   = ''   # isi lengkap identity.md
    ai_name: str         = 'Aria'   # diambil dari soul.md baris "**Nama:**"
    owner_name: str      = ''   # diambil dari identity.md baris "**Nama:**"
    owner_callname: str  = ''   # diambil dari identity.md baris "**Panggilan:**"
    owner_lang: str      = 'Indonesia'  # bahasa utama pemilik
    loaded_at: float     = field(default_factory=time.time)


# ─── PersonaLoader ───────────────────────────────────────────────────────────

class PersonaLoader:
    """
    Singleton loader untuk soul.md dan identity.md.

    Gunakan PersonaLoader.get() untuk mendapatkan instance global.
    Data di-cache dan di-reload otomatis jika file berubah (mtime check).
    """

    _instance: PersonaLoader | None = None

    # Interval minimal antara mtime check (detik)
    RELOAD_INTERVAL = 60.0

    def __init__(
        self,
        soul_path: str | Path | None = None,
        identity_path: str | Path | None = None,
    ) -> None:
        self._soul_path     = Path(soul_path)     if soul_path     else Path(os.environ.get('SOUL_FILE',     str(_DEFAULT_SOUL)))
        self._identity_path = Path(identity_path) if identity_path else Path(os.environ.get('IDENTITY_FILE', str(_DEFAULT_IDENTITY)))

        self._data: PersonaData = PersonaData()
        self._soul_mtime: float = 0.0
        self._identity_mtime: float = 0.0
        self._last_check: float = 0.0

        # Load sekali saat inisialisasi
        self._reload()

    # ── Singleton ──────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> 'PersonaLoader':
        """Kembalikan instance singleton. Buat jika belum ada."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def data(self) -> PersonaData:
        """Kembalikan PersonaData, reload jika file berubah."""
        now = time.monotonic()
        if now - self._last_check >= self.RELOAD_INTERVAL:
            self._last_check = now
            self._check_and_reload()
        return self._data

    @property
    def ai_name(self) -> str:
        return self.data.ai_name

    @property
    def owner_name(self) -> str:
        return self.data.owner_name

    @property
    def owner_callname(self) -> str:
        return self.data.owner_callname or self.data.owner_name

    def build_system_prompt(self, extra: str = '') -> str:
        """
        Susun system prompt lengkap: soul + identity + extra context.

        Returns:
            String siap pakai sebagai system message untuk LLM.
        """
        d = self.data
        parts: list[str] = []

        # ── Karakter AI ──────────────────────────────────────────────────
        if d.soul_text:
            parts.append(
                '## Karakter & Persona\n\n'
                + d.soul_text.strip()
            )
        else:
            parts.append(
                f'## Karakter & Persona\n\n'
                f'Kamu adalah {d.ai_name}, asisten AI personal yang cerdas dan membantu.'
            )

        # ── Identitas pemilik ────────────────────────────────────────────
        if d.identity_text:
            parts.append(
                '## Tentang Pemilik\n\n'
                + d.identity_text.strip()
            )
        elif d.owner_name:
            parts.append(
                f'## Tentang Pemilik\n\n'
                f'Nama pemilik: {d.owner_name}. Sapa dengan nama tersebut.'
            )

        # ── Aturan sapaan ────────────────────────────────────────────────
        if d.owner_callname:
            parts.append(
                f'## Instruksi Sapaan\n\n'
                f'Panggil pemilik dengan "{d.owner_callname}". '
                f'Jangan gunakan panggilan lain kecuali diminta.'
            )

        # ── Extra context (memory, dll) ──────────────────────────────────
        if extra:
            parts.append(extra.strip())

        return '\n\n---\n\n'.join(parts)

    def build_browser_instruction(self) -> str:
        """
        Instruksi singkat untuk browser-use extend_system_message.
        Lebih ringkas dari build_system_prompt() karena browser agent
        punya system prompt sendiri yang panjang.
        """
        d = self.data
        lines: list[str] = []

        if d.ai_name and d.ai_name != 'Aria':
            lines.append(f'You are {d.ai_name}, an AI assistant.')

        if d.owner_callname:
            lines.append(f'You are working for {d.owner_callname}.')

        if d.soul_text:
            # Ambil hanya bagian Nilai & Batasan dari soul.md jika ada
            match = re.search(
                r'##\s*Nilai.*?\n(.*?)(?=\n##|\Z)',
                d.soul_text, re.DOTALL | re.IGNORECASE
            )
            if match:
                lines.append('Guidelines: ' + match.group(1).strip()[:300])

        lines.append(
            'IMPORTANT: Whenever you take a screenshot, ALWAYS provide a '
            'file_name parameter (e.g. file_name="screenshot_step1") so the '
            'image is saved to disk and can be sent back to the user.'
        )

        return '\n'.join(lines)

    # ── Internal ──────────────────────────────────────────────────────────

    def _check_and_reload(self) -> None:
        """Reload file jika mtime berubah sejak terakhir load."""
        soul_mtime     = self._mtime(self._soul_path)
        identity_mtime = self._mtime(self._identity_path)

        if soul_mtime != self._soul_mtime or identity_mtime != self._identity_mtime:
            self._reload()

    def _reload(self) -> None:
        """Baca ulang kedua file dan parse ulang PersonaData."""
        soul_text     = self._read(self._soul_path)
        identity_text = self._read(self._identity_path)

        self._data = PersonaData(
            soul_text=soul_text,
            identity_text=identity_text,
            ai_name=self._parse_field(soul_text, 'Nama') or 'Aria',
            owner_name=self._parse_field(identity_text, 'Nama') or '',
            owner_callname=self._parse_field(identity_text, 'Panggilan') or '',
            owner_lang=self._parse_field(identity_text, 'Bahasa utama') or 'Indonesia',
        )

        self._soul_mtime     = self._mtime(self._soul_path)
        self._identity_mtime = self._mtime(self._identity_path)
        self._last_check     = time.monotonic()

        logger.info(
            f'Persona loaded — AI: {self._data.ai_name!r}, '
            f'Owner: {self._data.owner_callname or self._data.owner_name or "(unknown)"!r}'
        )

    @staticmethod
    def _read(path: Path) -> str:
        """Baca file, kembalikan string kosong jika tidak ada."""
        try:
            return path.read_text(encoding='utf-8')
        except FileNotFoundError:
            logger.debug(f'Persona file tidak ditemukan: {path}')
            return ''
        except Exception as e:
            logger.warning(f'Gagal baca {path}: {e}')
            return ''

    @staticmethod
    def _mtime(path: Path) -> float:
        """Kembalikan mtime file, 0 jika tidak ada."""
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _parse_field(text: str, field_name: str) -> str:
        """
        Parse nilai dari baris format: **Nama:** nilai
        Mendukung variasi: '**Nama:**', 'Nama:', '- Nama:' dll.
        """
        # Match: **Nama:** nilai  atau  Nama: nilai
        pattern = rf'\*{{0,2}}{re.escape(field_name)}\*{{0,2}}\s*:\s*(.+)'
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            # Ambil teks sampai akhir baris, strip markdown formatting
            raw = match.group(1).strip()
            raw = re.sub(r'\*+', '', raw)   # hapus bold/italic markers
            raw = raw.split('(')[0].strip() # buang komentar dalam kurung
            return raw
        return ''


# ─── Module-level singleton helper ───────────────────────────────────────────

def get_persona() -> PersonaLoader:
    """Shortcut: kembalikan singleton PersonaLoader."""
    return PersonaLoader.get()
