# mybrowse

**Multi-agent AI yang bisa browsing web, menjawab pertanyaan, dan mengingat percakapan — dikendalikan lewat Telegram.**

Cukup kirim pesan biasa. Sistem otomatis memilih agent terbaik: browser untuk internet, chat untuk reasoning, memory untuk preferensi. Tidak perlu prefix atau command khusus.

---

## Daftar Isi

- [Demo](#demo)
- [Fitur](#fitur)
- [Arsitektur](#arsitektur)
- [Prasyarat](#prasyarat)
- [Instalasi](#instalasi)
- [Konfigurasi](#konfigurasi)
  - [1. LLM (OpenAI / OpenAI-compatible)](#1-llm-openai--openai-compatible)
  - [2. Telegram Bot Token](#2-telegram-bot-token)
  - [3. Database PostgreSQL](#3-database-postgresql)
  - [4. Chrome Browser](#4-chrome-browser)
- [Menjalankan Aplikasi](#menjalankan-aplikasi)
  - [Mode Telegram (utama)](#mode-telegram-utama)
  - [Mode CLI (single task)](#mode-cli-single-task)
- [Perintah Telegram](#perintah-telegram)
- [Cara Penggunaan](#cara-penggunaan)
- [Menambah Agent Baru](#menambah-agent-baru)
- [Menambah Channel Baru](#menambah-channel-baru)
- [Struktur Proyek](#struktur-proyek)
- [Pengembangan](#pengembangan)
- [FAQ](#faq)

---

## Demo

```
User: cari harga iPhone 16 termurah di tokopedia

Bot: ⏳ Memproses...
     🌐 Agent Berjalan | Waktu: 8s [████░░░░░░░░░░░░]
     Status: [browser] step 3: navigate → Membuka halaman hasil pencarian...

Bot: ✅ Hasil

     iPhone 16 termurah di Tokopedia:
     - iPhone 16 128GB: Rp 12.499.000
     - iPhone 16 256GB: Rp 13.999.000
     - iPhone 16 512GB: Rp 16.499.000

[Screenshot halaman dikirim otomatis]
```

```
User: jelaskan perbedaan RAM dan ROM

Bot: 💬 Menjawab langsung...

     RAM (Random Access Memory) adalah memori sementara...
     ROM (Read-Only Memory) adalah memori permanen...
```

```
User: ingat bahwa saya lebih suka jawaban dalam poin-poin singkat

Bot: 🧠 Tersimpan: "saya lebih suka jawaban dalam poin-poin singkat"
```

---

## Fitur

| Fitur | Keterangan | Status |
|---|---|---|
| Natural language input | Kirim pesan biasa, tanpa prefix `/task` | ✅ |
| Automatic agent routing | LLM memilih browser / chat / memory secara otomatis | ✅ |
| Browser automation | Navigasi web, klik, isi form, screenshot, scraping | ✅ |
| Chat / Reasoning | Q&A, penjelasan, penulisan, kalkulasi langsung dari LLM | ✅ |
| Multi-turn conversation | Konteks percakapan diingat antar pesan dalam satu sesi | ✅ |
| Long-term memory | Hasil task disimpan ke DB, diinjeksikan ke task berikutnya | ✅ |
| Telegram channel | Live progress, inline keyboard, typing indicator, file delivery | ✅ |
| Task cancellation | `/cancel` untuk menghentikan task yang sedang berjalan | ✅ |
| Screenshot delivery | Gambar screenshot dikirim langsung ke Telegram | ✅ |
| Task history | Semua task tersimpan di PostgreSQL dengan status & durasi | ✅ |
| CLI mode | Jalankan satu task dari terminal | ✅ |
| WhatsApp channel | Planned | 🔜 |
| Discord channel | Planned | 🔜 |

---

## Arsitektur

```
User (Telegram / CLI)
    │
    ▼
Channel Layer (TelegramChannel / CLI)
    │  handle_message(task, channel_id, username, on_update)
    ▼
BaseChannel → Supervisor.run(AgentContext)
    │
    ├─ [1] Fetch long-term memory dari DB
    ├─ [2] Inject conversation history (multi-turn)
    ├─ [3] task_create di DB
    ├─ [4] LLM routing → pilih agent
    │
    ├──► 🌐 BrowserAgent   (browser-use: navigasi, klik, screenshot)
    ├──► 💬 ChatAgent       (AsyncOpenAI: Q&A, reasoning, penulisan)
    └──► 🧠 MemoryAgent    (asyncpg: simpan/recall/hapus memory)
    │
    ├─ [5] task_done, step_log, attachment_save di DB
    ├─ [6] auto-save hasil ke memory
    └─ [7] update conversation history
    │
    ▼
SupervisorResult → Channel → User
```

**Tech stack:**

| Layer | Teknologi |
|---|---|
| Language | Python 3.12 |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Browser automation | [browser-use](https://github.com/browser-use/browser-use) |
| LLM | OpenAI / OpenAI-compatible endpoint |
| Database | PostgreSQL 17+ via asyncpg |
| Telegram API | aiohttp (long polling) |
| Config | python-dotenv |

---

## Prasyarat

Pastikan sudah terinstall:

- **Python 3.12+** — cek dengan `python --version`
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — package manager (wajib, jangan gunakan pip)
- **PostgreSQL 17+** — running di `localhost:5432`
- **Google Chrome** — untuk browser agent
- **Telegram account** — untuk mendapatkan bot token

---

## Instalasi

```bash
# 1. Clone repository
git clone https://github.com/tamaproject360/mybrowse.git
cd mybrowse

# 2. Buat virtual environment (Python 3.12 wajib)
uv venv --python 3.12

# 3. Aktifkan environment
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

# 4. Install semua dependencies
uv sync

# 5. Salin file konfigurasi
cp .env.example .env
```

---

## Konfigurasi

Edit file `.env` yang baru dibuat. Berikut panduan lengkap untuk setiap nilai yang diperlukan:

### 1. LLM (OpenAI / OpenAI-compatible)

**Opsi A — OpenAI (cloud):**

1. Buka [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Klik **"Create new secret key"**
3. Salin key dan masukkan ke `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o
```

**Opsi B — Local LLM (Ollama, LM Studio, dll):**

```env
OPENAI_API_KEY=ollama          # nilai apapun, tidak diverifikasi
OPENAI_BASE_URL=http://localhost:11434/v1   # sesuaikan dengan port lokal
OPENAI_MODEL=llama3.2          # nama model yang sedang berjalan
```

**Opsi C — API lain yang OpenAI-compatible (Groq, Together, Mistral, dll):**

```env
OPENAI_API_KEY=gsk_...         # key dari provider
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile
```

> **Rekomendasi model:** Gunakan model dengan kemampuan function calling / tool use yang baik. GPT-4o, Claude Sonnet, atau Llama 3.3 70B direkomendasikan untuk routing dan browser tasks.

---

### 2. Telegram Bot Token

**Langkah mendapatkan token:**

1. Buka Telegram, cari `@BotFather`
2. Kirim pesan `/newbot`
3. Masukkan **nama bot** (contoh: `mybrowse Agent`)
4. Masukkan **username bot** — harus diakhiri `bot` (contoh: `mybrowse_agent_bot`)
5. BotFather akan mengirim token seperti: `7123456789:AAHdqTcvCH1vGWJxfSeofSznPxpSN9aE`
6. Masukkan ke `.env`:

```env
TELEGRAM_BOT_TOKEN=7123456789:AAHdqTcvCH1vGWJxfSeofSznPxpSN9aE
```

**Mendapatkan Chat ID (untuk TELEGRAM_ALLOWED_USERS):**

Jika ingin membatasi siapa yang bisa menggunakan bot:

1. Start bot yang sudah dibuat di Telegram
2. Buka: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Kirim pesan ke bot, lalu refresh URL di atas
4. Cari `"chat":{"id":` — angka di sana adalah chat ID kamu
5. Atau gunakan `@userinfobot` di Telegram untuk mendapatkan ID kamu

```env
# Kosongkan untuk mengizinkan semua orang (tidak disarankan untuk bot privat)
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

---

### 3. Database PostgreSQL

**Setup database:**

```bash
# Masuk ke PostgreSQL
psql -U postgres

# Buat database
CREATE DATABASE mybrowse;
\q
```

**Buat tabel (jalankan query berikut di psql atau pgAdmin):**

```sql
-- Koneksi ke database mybrowse dulu
\c mybrowse

CREATE TABLE tasks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ,
    channel     VARCHAR(50) NOT NULL,
    channel_id  VARCHAR(100) NOT NULL,
    username    VARCHAR(100),
    prompt      TEXT NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    output      TEXT,
    success     BOOLEAN,
    steps       INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER
);

CREATE TABLE step_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    step_num    INTEGER NOT NULL,
    actions     TEXT[],
    next_goal   TEXT,
    evaluation  TEXT,
    url         VARCHAR(2048)
);

CREATE TABLE attachments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id          UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    file_name        VARCHAR(255),
    file_path        TEXT,
    file_type        VARCHAR(50),
    mime_type        VARCHAR(100),
    size_bytes       INTEGER,
    sent_to_channel  BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel     VARCHAR(50) NOT NULL,
    channel_id  VARCHAR(100) NOT NULL,
    username    VARCHAR(100),
    content     TEXT NOT NULL,
    mem_type    VARCHAR(50) NOT NULL DEFAULT 'general',
    source      VARCHAR(50),
    task_id     UUID REFERENCES tasks(id) ON DELETE SET NULL
);

-- Index untuk performa query
CREATE INDEX idx_tasks_channel ON tasks(channel, channel_id);
CREATE INDEX idx_memories_channel ON memories(channel, channel_id);
CREATE INDEX idx_step_logs_task ON step_logs(task_id);
CREATE INDEX idx_attachments_task ON attachments(task_id);
```

**Konfigurasi di `.env`:**

```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/mybrowse
```

Sesuaikan `postgres:password` dengan username dan password PostgreSQL kamu.

---

### 4. Chrome Browser

mybrowse menggunakan Google Chrome untuk browser automation.

**Windows:**
```env
CHROME_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
```

**macOS:**
```env
CHROME_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

**Linux:**
```env
CHROME_PATH=/usr/bin/google-chrome
```

Jika Chrome belum terinstall, download dari [google.com/chrome](https://www.google.com/chrome/).

---

### File `.env` Lengkap

```env
# ── LLM ──────────────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=7123456789:AAHdqTcvCH1vGWJxfSeofSznPxpSN9aE
TELEGRAM_ALLOWED_USERS=123456789        # kosong = semua diizinkan

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql://postgres:password@localhost:5432/mybrowse

# ── Browser ───────────────────────────────────────────────────────────────────
CHROME_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
AGENT_HEADLESS=false                    # true = tanpa jendela browser
AGENT_MAX_STEPS=50                      # max langkah per browser task

# ── Opsional ──────────────────────────────────────────────────────────────────
ANONYMIZED_TELEMETRY=false
BROWSER_USE_LOGGING_LEVEL=info
```

---

## Menjalankan Aplikasi

> **Windows:** Selalu gunakan prefix `PYTHONUTF8=1` agar emoji dan karakter Unicode ditampilkan dengan benar.

### Mode Telegram (utama)

```bash
# Windows
PYTHONUTF8=1 .venv/Scripts/python.exe run.py --telegram

# Linux / macOS
PYTHONUTF8=1 python run.py --telegram
```

Output saat berhasil:
```
2026-02-25 10:00:00 [INFO] mybrowse: Telegram bot berjalan. Ctrl+C untuk berhenti.
2026-02-25 10:00:00 [INFO] TelegramChannel: Telegram bot @mybrowse_agent_bot terhubung. Polling...
2026-02-25 10:00:00 [INFO] db: Database pool created
```

Kemudian buka Telegram, cari bot kamu, dan kirim `/start`.

Untuk menghentikan: tekan `Ctrl+C`.

### Mode CLI (single task)

```bash
# Jalankan satu task langsung dari terminal
PYTHONUTF8=1 .venv/Scripts/python.exe run.py "cari harga laptop gaming di tokopedia"

# Task default (test koneksi)
PYTHONUTF8=1 .venv/Scripts/python.exe run.py
```

Output:
```
Task: cari harga laptop gaming di tokopedia
────────────────────────────────────────────────────────────
  → Menganalisis task...
  → 🌐 Menggunakan browser agent...
  → [browser] step 1: navigate → Membuka tokopedia.com...
  → [browser] step 2: search → Mencari laptop gaming...
────────────────────────────────────────────────────────────
Agent: browser
Status: ✓ Selesai
Langkah: 5

Laptop gaming termurah di Tokopedia:
- ASUS TUF Gaming F15: Rp 8.999.000
...
```

---

## Perintah Telegram

| Perintah | Fungsi |
|---|---|
| `/start` | Menu utama dan welcome message |
| `/status` | Cek apakah ada task yang sedang berjalan |
| `/cancel` | Batalkan task yang sedang berjalan |
| `/history` | Lihat 5 task terakhir |
| `/memory` | Tampilkan semua memory yang tersimpan |
| `/forget` | Hapus semua memory untuk chat ini |
| `/clear` | Reset riwayat percakapan (multi-turn context) |
| `/help` | Panduan penggunaan lengkap |

**Pesan biasa** (tanpa `/`) langsung diproses oleh Supervisor — tidak perlu prefix apapun.

---

## Cara Penggunaan

### Browsing web

```
cari harga iPhone 16 di tokopedia
buka twitter dan lihat trending topic
screenshot halaman utama github.com
login ke gmail dan cek email terbaru
scraping 5 artikel terbaru dari detik.com
```

### Chat dan reasoning

```
jelaskan apa itu transformer dalam machine learning
tulis email formal untuk permohonan cuti 3 hari
berapa hasil dari (256 * 13) / 4?
rangkum teks berikut dalam 3 poin: [paste teks]
beri contoh kode Python untuk sorting list
```

### Memory

```
ingat bahwa saya tinggal di Jakarta
ingat bahwa saya lebih suka jawaban singkat dalam poin-poin
kamu ingat apa tentang saya?
hapus semua yang kamu ingat tentang saya
```

### Multi-turn conversation

Setelah menjawab satu pertanyaan, kamu bisa langsung follow-up:

```
User: jelaskan apa itu Python

Bot: Python adalah bahasa pemrograman...

User: beri contoh kodenya
     (tanpa menyebut "Python" lagi — bot sudah ingat konteksnya)
```

---

## Menambah Agent Baru

1. Buat file `agents/myagent.py`:

```python
from agents.base import AgentContext, AgentResult, BaseAgent

class MyAgent(BaseAgent):
    name = 'myagent'
    description = (
        'Deskripsi singkat kapan agent ini dipakai. '
        'LLM routing menggunakan teks ini untuk memilih agent.'
    )

    async def run(self, ctx: AgentContext) -> AgentResult:
        # implementasi agent
        result = do_something(ctx.task)
        return AgentResult(success=True, output=result, agent_name=self.name)
```

2. Daftarkan di `run.py`:

```python
from agents.myagent import MyAgent
supervisor.register_agent(MyAgent(llm=LLM))
```

Supervisor LLM otomatis akan mulai merouting task yang relevan ke agent baru.

---

## Menambah Channel Baru

1. Buat folder dan file `channels/mychannel/channel.py`:

```python
from channels.base import BaseChannel
from agents.supervisor import SupervisorResult

class MyChannel(BaseChannel):
    async def start(self) -> None:
        # mulai menerima pesan (polling, webhook, dll)
        while True:
            message = await receive_message()
            result: SupervisorResult = await self.handle_message(
                task=message.text,
                channel='mychannel',
                channel_id=str(message.user_id),
                username=message.username,
            )
            await send_reply(message.user_id, result.output)

    async def stop(self) -> None:
        pass  # cleanup
```

2. Inisialisasi di `run.py` mengikuti pola yang sama dengan `TelegramChannel`.

---

## Struktur Proyek

```
mybrowse/
│
├── run.py                      # Entry point: --telegram atau CLI mode
├── db.py                       # Database layer (asyncpg, tanpa ORM)
├── .env                        # Konfigurasi (jangan di-commit)
├── .env.example                # Template konfigurasi
├── pyproject.toml              # Dependencies (uv)
│
├── agents/                     # Multi-agent system
│   ├── base.py                 # AgentContext, AgentResult, BaseAgent, BrowserConfig
│   ├── supervisor.py           # Supervisor: routing + DB + history + memory
│   ├── browser.py              # BrowserAgent: autonomous web browsing
│   ├── chat.py                 # ChatAgent: LLM langsung, multi-turn history
│   └── memory.py               # MemoryAgent: CRUD memory ke DB
│
├── channels/                   # Channel layer (plug & play)
│   ├── base.py                 # BaseChannel: wrapper tipis Supervisor.run()
│   └── telegram/
│       └── channel.py          # TelegramChannel: polling, progress, keyboard
│
├── screenshots/                # Screenshot hasil browser agent (auto-dibuat)
│
├── docs/
│   └── specs.md                # Architecture Guidelines (source of truth)
│
└── browser_use/                # Library browser-use (JANGAN DIMODIFIKASI)
```

---

## Pengembangan

### Setup development

```bash
# Clone dan install dengan dev dependencies
git clone https://github.com/tamaproject360/mybrowse.git
cd mybrowse
uv venv --python 3.12
source .venv/bin/activate   # atau .venv\Scripts\activate di Windows
uv sync
cp .env.example .env
# Edit .env dengan nilai-nilai kamu
```

### Konvensi kode

- Gunakan **absolute imports** — tidak boleh `from . import`
- Semua DB call harus di dalam `try/except` — tidak boleh crash main flow
- `asyncio.CancelledError` **wajib selalu di-re-raise** (dipakai untuk `/cancel`)
- Jangan pernah modifikasi file di dalam `browser_use/`
- Format dengan pre-commit sebelum commit

### Variabel environment untuk debug

```env
BROWSER_USE_LOGGING_LEVEL=debug    # log detail browser-use
AGENT_HEADLESS=false               # tampilkan jendela browser saat development
AGENT_MAX_STEPS=10                 # batasi langkah agar testing lebih cepat
```

### Menjalankan test

```bash
# Test koneksi DB dan LLM
PYTHONUTF8=1 .venv/Scripts/python.exe -c "
from dotenv import load_dotenv; load_dotenv()
import asyncio, db
async def t():
    pool = await db.get_pool()
    print('DB OK')
    await db.close_pool()
asyncio.run(t())
"

# Test single task via CLI
PYTHONUTF8=1 .venv/Scripts/python.exe run.py "berapa 2+2?"
```

---

## FAQ

**Apakah bisa menggunakan model selain OpenAI?**

Ya. Semua endpoint yang OpenAI-compatible bisa digunakan: Ollama, LM Studio, Groq, Together, Mistral, dll. Cukup set `OPENAI_BASE_URL` dan `OPENAI_MODEL` sesuai provider.

**Browser tidak terbuka / crash?**

Pastikan `CHROME_PATH` di `.env` menunjuk ke executable Chrome yang benar. Coba set `AGENT_HEADLESS=false` untuk melihat apa yang terjadi. Pada server tanpa display, set `AGENT_HEADLESS=true`.

**Bot Telegram tidak merespons?**

1. Pastikan `TELEGRAM_BOT_TOKEN` benar (test di browser: `https://api.telegram.org/bot<TOKEN>/getMe`)
2. Jika `TELEGRAM_ALLOWED_USERS` diset, pastikan chat ID kamu sudah terdaftar
3. Cek log terminal untuk error

**Database error saat startup?**

Pastikan PostgreSQL berjalan dan `DATABASE_URL` sudah benar. Bot tetap bisa berjalan tanpa DB (semua operasi DB non-fatal), tapi fitur history dan memory tidak akan berfungsi.

**Bagaimana cara menghentikan bot?**

Tekan `Ctrl+C` di terminal. Bot akan graceful shutdown: membatalkan semua task yang berjalan, menutup koneksi DB.

**Memory vs conversation history — apa bedanya?**

- **Memory** (`/memory`, `/forget`) — disimpan ke database, bertahan lintas sesi dan restart
- **Conversation history** (`/clear`) — disimpan in-memory, hilang saat bot restart, berisi konteks percakapan aktif

---

## Lisensi

MIT License — lihat [LICENSE](LICENSE) untuk detail.

---

<div align="center">
  <b>mybrowse</b> — AI agent yang bekerja untuk kamu
</div>
