# File Bot

_Bot by [@akaza_isnt](https://t.me/akaza_isnt)._

A streaming Telegram **file-splitter / re-uploader / cookie-extractor** built with
[Pyrogram] (MTProto), so it bypasses the 20 MB Bot HTTP API ceiling and can
handle files up to **2 GB per part**.

## Features

- **Forward me a file** — I'll re-upload it back to you, optionally split.
- **Log to Cookies** — extract cookies from logs smartly by domain.
- Support for **.zip, .rar, .7z** archives for cookie extraction.
- Four split modes:
  - 📦 **By size** (e.g. `100 MB`)
  - #️⃣ **By count** (e.g. `20 parts`)
  - 🤖 **Auto** (~50 MB chunks)
  - ⏩ **No split**
- **Smart text-aware splitter** — `.txt` / `.csv` / `.json` / `.py` / `.md`
  / `.log` / `.sql` etc. are split on the nearest line boundary.
- **Elastic disk budget** — refuses jobs that would fill the volume.
- **Per-user daily quota**.
- **Single-user queue** — only one non-VIP job runs at a time.
- **Interactive `/help`** with category buttons.
- **Request-based access control**.

## Credentials

Set these env vars:

| Var | Where to get it |
| --- | --- |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `API_ID` | https://my.telegram.org/apps → "Create new application" |
| `API_HASH` | (same page) |
| `ADMIN_ID` | Your numeric Telegram user ID |

## Local quickstart

```bash
git clone https://github.com/speedhub-bot/File-bot.git
cd File-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

## Architecture

```
forwarded file ────┐
                   │   ┌────────────────────────────────┐
                   ├──▶│  on_file (handlers/files.py)   │
                   │   │   - normal split flow          │
                   │   └────────────┬───────────────────┘
                   │                │
                   │                ▼
                   │   ┌────────────────────────────────┐
                   │   │  JobManager (services/jobs.py) │
                   │   └────────────────────────────────┘
                   │
Log to Cookies ────▶ handlers/cookies.py -> services/cookies.py
```

## License

MIT — see `LICENSE`.

[Pyrogram]: https://docs.pyrogram.org/
