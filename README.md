# File Bot

_Bot by [@akaza_isnt](https://t.me/akaza_isnt)._

A streaming Telegram **file-splitter / re-uploader / merger** built with
[Pyrogram] (MTProto), so it bypasses the 20 MB Bot HTTP API ceiling and can
handle files up to **2 GB per part**. Designed to run on a 1 GB Railway free
tier (or any small VPS) without ever buffering whole files in RAM.

## Features

- **Forward me a file** — I'll re-upload it back to you, optionally split.
- **`/url <link>`** — give me a direct HTTP(S) URL, I'll stream-download
  it and run the same split flow.
- **`/merge`** — collect previously-split parts and join them back together.
- Four split modes:
  - 📦 **By size** (e.g. `100 MB`)
  - #️⃣ **By count** (e.g. `20 parts`)
  - 🤖 **Auto** (~50 MB chunks)
  - ⏩ **No split**
- **Smart text-aware splitter** — `.txt` / `.csv` / `.json` / `.py` / `.md`
  / `.log` / `.sql` etc. are split on the nearest line boundary (with
  fallback to whitespace) so no sentence is cut mid-word. Binary files
  split at exact byte offsets.
- **Elastic disk budget** — refuses jobs that would fill the volume.
  Default is `0` (no explicit cap, just respects free space minus
  ~512 MB of slack); set `DISK_BUDGET_BYTES` to a fixed number for
  Railway/Fly free tiers.
- **Per-user daily quota** (default `0` = unlimited; set
  `PER_USER_DAILY_BYTES` to e.g. `10737418240` for a 10 GB/day cap).
- **Single-user queue** — only one non-VIP job runs at a time so users
  don't crash each other on the 1 GB volume; queued users get a clear
  "please wait" notice. **Admin and VIPs bypass the queue.**
- **Concurrency cap** — global semaphore (default 2 jobs at a time).
- **Per-part immediate deletion** keeps disk footprint flat regardless of
  total file size.
- **Live progress messages** with edit-rate limiting (1 edit/sec).
- **HTTP `/health` + `/ready` endpoints** so Railway / Fly / Koyeb
  don't kill the container for failing healthchecks.
- **Profile card** — `/profile` shows your stats, role, and quota.
- **Interactive `/help`** with category buttons (Files / URL / Merge /
  Profile / Queue / Admin).
- **Admin commands**: `/stats`, `/jobs`, `/users`, `/broadcast`,
  `/ban`, `/unban`, `/cleanup`, `/diag`, `/grant`, `/revokevip`,
  `/info`, `/echo`, `/restart`.

## Credentials

The bot ships with **embedded default credentials** in `bot/config.py`,
so a fresh deploy on Railway / Fly / Docker / a bare VPS just works
without setting any environment variables.

> ⚠️ **Security tradeoff.** Because this repo is public, the embedded
> token is also public. Anyone who finds it can run their own copy of
> the bot identity. If you care about that:
>
> 1. Open [@BotFather](https://t.me/BotFather) → `/revoke` → pick the
>    bot → grab the new token.
> 2. Either replace `DEFAULT_BOT_TOKEN` in `bot/config.py` *or* set
>    `BOT_TOKEN` as a Railway/Fly Variable (env vars always win over the
>    defaults).
> 3. Same goes for the api_id/api_hash if you want a fully-private app.
>
> The bot prints a `⚠️ Bot is running on the embedded default
> credentials` warning at every startup so you don't forget.

If you'd rather run with your *own* bot identity from day one, set these
four env vars and they'll override the defaults:

| Var | Where to get it |
| --- | --- |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `API_ID` | https://my.telegram.org/apps → "Create new application" |
| `API_HASH` | (same page) |
| `ADMIN_ID` | Your numeric Telegram user ID — DM [@userinfobot](https://t.me/userinfobot) |

## Local quickstart

```bash
git clone https://github.com/speedhub-bot/File-bot.git
cd File-bot
cp .env.example .env
# edit .env and fill in BOT_TOKEN / API_ID / API_HASH / ADMIN_ID
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

You should see `Logged in as @yourbot (id=…)` and a `Health server
listening on 0.0.0.0:8080` line.

## Docker

```bash
docker build -t filebot .
docker run --rm -it \
    -e BOT_TOKEN=… -e API_ID=… -e API_HASH=… -e ADMIN_ID=… \
    -v $(pwd)/data:/data \
    -p 8080:8080 \
    filebot
```

## Railway

1. **New Project → Deploy from GitHub repo** → pick your fork of File-bot.
2. **Variables** tab — add `BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_ID`.
3. **Volumes** — mount one at `/data` (1 GB on the free tier).
4. Railway auto-detects the Dockerfile + `railway.toml` and uses
   `/health` for healthchecks. The bot expects `$PORT` (Railway sets
   it) and binds the health server to it automatically.

If a deploy crashes, hit `/diag` from your admin account on Telegram —
it prints the runtime env / disk / port info so you can pinpoint the
problem without SSHing in.

## Fly.io

```bash
fly launch --no-deploy        # pick a region
fly volumes create data --size 3   # 3 GB persistent volume
fly secrets set BOT_TOKEN=… API_ID=… API_HASH=… ADMIN_ID=…
fly deploy
```

The provided Dockerfile is Fly-compatible; the `/health` endpoint is
what Fly hits on `$PORT`.

## Tuning

All tunables are env vars (see `.env.example`):

| Var | Default | Meaning |
| --- | --- | --- |
| `DISK_BUDGET_BYTES` | `0` (unlimited — just leaves ~512 MB free) | Hard cap on the working dir; set to e.g. `943718400` (~900 MB) for Railway free |
| `PER_USER_DAILY_BYTES` | `0` (unlimited) | Per-user daily cap; set to e.g. `10737418240` (~10 GB) to enforce |
| `DEFAULT_AUTO_PART_BYTES` | `52428800` (50 MB) | Target part size for "🤖 Auto" |
| `MAX_PART_BYTES` | `2093796556` (~1.95 GB) | Telegram per-document cap |
| `MAX_CONCURRENT_JOBS` | `2` | Global concurrency cap |
| `WORK_DIR` | `./work` (`/data/work` in Docker) | Where downloads + parts live |
| `DATABASE_URL` | `sqlite+aiosqlite:///./filebot.db` | SQLite path |
| `PORT` | `8080` | Health server port |

## Commands

### User commands
- `/start` — welcome message + buttons
- `/help` — interactive help with category buttons
- `/profile` — your stats card (jobs, bytes, daily quota left, role)
- `/url <link>` — stream-download a URL and split
- `/merge start` / `/merge done <name>` / `/merge cancel` / `/merge status`
- `/cancel` — abort a pending split prompt
- Forward any file to the bot to start the normal split flow

### Admin-only commands
- `/stats` — user count, jobs, disk usage, budget remaining
- `/jobs` — currently running + last 10 completed
- `/users` — top 30 users by total bytes processed
- `/broadcast <message>` — send a message to every non-banned user
- `/echo <chat_id> <message>` — DM a single chat as the bot
- `/ban <id>` / `/unban <id>` — toggle bot access
- `/grant <id>` / `/revokevip <id>` — promote/demote VIP (skips queue +
  daily quota)
- `/info <id>` — full record for a user
- `/cleanup` — wipe stale `job-*` directories from `WORK_DIR`
- `/restart` — exit(0); platform restarts the container
- `/diag` — runtime diagnostics (Python / Pyrogram / disk / port / env)

## Architecture

```
forwarded file ────┐
                   │   ┌────────────────────────────────┐
                   ├──▶│  on_file (handlers/files.py)   │
                   │   │   - sanitizes filename         │
/url <link> ───────┤   │   - checks ban + quota         │
                   │   │   - stores in PENDING          │
                   │   └────────────┬───────────────────┘
                   │                │ user picks split mode
                   │                ▼
                   │   ┌────────────────────────────────┐
                   │   │  JobManager (services/jobs.py) │
                   │   │   - global asyncio.Semaphore   │
                   │   │   - download → split → upload  │
                   │   │   - per-part rm after upload   │
                   │   └────────────┬───────────────────┘
                   │                │
                   │                ▼
                   │   ┌────────────────────────────────┐
                   │   │  splitter (services/splitter) │
                   │   │   - text mode: line-aware      │
                   │   │   - binary: exact byte offsets │
                   │   │   - 1 MiB streaming buffer     │
                   │   └────────────────────────────────┘
                   │
                   └──▶ /merge collects parts, joins, sends
```

A tiny aiohttp server runs on `$PORT` exposing `/health` (always 200
once up) and `/ready` (503 when disk is nearly full) so the platform's
healthcheck has something to talk to.

## License

MIT — see `LICENSE`.

[Pyrogram]: https://docs.pyrogram.org/
