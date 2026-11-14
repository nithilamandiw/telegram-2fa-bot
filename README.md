<div align="center">

<h1>🔐 Telegram 2FA Bot</h1>

<p>Temporary authentication codes, right in Telegram.</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.12 or newer">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&amp;logo=docker&amp;logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Session-5_minutes-7C3AED?style=flat-square" alt="Five-minute sessions">
  <img src="https://img.shields.io/badge/TOTP-Memory_only-475569?style=flat-square" alt="TOTP sessions stored in memory only">
</p>

<p>
  <a href="#local-setup">Local setup</a> ·
  <a href="#server-setup">Server setup</a> ·
  <a href="#usage">Usage</a> ·
  <a href="docs/EC2.md">EC2 guide</a> ·
  <a href="#development">Development</a>
</p>

</div>

---

After owner approval, send a TOTP secret in a private chat. Get a code that refreshes automatically for **five minutes**, with a countdown showing when the code changes and when the session ends.

| ⏱️ Temporary sessions | 👤 Separate for each user |
| --- | --- |
| Five-minute lifetime with an early **Stop** button. | Each user can view and control only their own session. |
| **📊 Live countdowns** | **🧠 Memory-only TOTP sessions** |
| Code and session time bars update about every five seconds. | No saved secrets, codes, or session backups. SQLite stores access decisions only. |

> [!IMPORTANT]
> Telegram bot chats are not end-to-end encrypted. Telegram and the running bot process submitted secrets. Message deletion is best effort.

## Preview

```text
Your 2FA code: 123456
Refresh in: ~25s
▓▓▓▓▓▓▓▓▓░

Session remaining: ~04:55
▓▓▓▓▓▓▓▓▓▓

[ Refresh ]  [ Stop ]
```

*Illustrative code. Each code keeps its normal validity period; the five-minute limit applies to the session.*

## Local setup

**Requirements:** Git, Docker Engine, Docker Compose v2, and a Telegram bot token.

### 1. Create your bot

Open [@BotFather](https://t.me/BotFather), create a bot, and obtain its token. Disable group joining with `/setjoingroups` and inline mode with `/setinline` → `/empty`.

### 2. Clone and configure

```sh
git clone https://github.com/nithilamandiw/telegram-2fa-bot.git
cd telegram-2fa-bot
cp .env.example .env
chmod 600 .env
```

Edit `.env` and set your BotFather token and **your own numeric Telegram user ID**:

```dotenv
BOT_TOKEN=your_botfather_token
DISPLAY_TIMEZONE=Asia/Colombo
OWNER_TELEGRAM_ID=123456789
AUTH_DB_PATH=data/access.sqlite3
```

Replace the example owner ID with your own user ID, not a username or the bot's ID. Missing or invalid owner configuration prevents startup. If you do not know your ID, use [Owner ID lookup](#owner-id-lookup) before starting.

`DISPLAY_TIMEZONE` accepts an IANA timezone. Keep `.env` out of Git and use it only for bot configuration, never TOTP secrets. Keep the default database path when using the supplied Docker volume.

The image sets code permissions explicitly and checks importing the application as the non-root bot user during the build. Restrictive host file permissions must not prevent the container from reading its code; `.env` remains private and is excluded from the image.

### 3. Start

```sh
docker compose up -d --build
docker compose ps
```

When the service shows **Up**, open your bot in Telegram and send `/start`. Run **one instance per bot token**.

### Owner ID lookup

Keep all bot instances stopped, set `BOT_TOKEN` in `.env`, then send `/whoami` to your bot in Telegram. It will not reply while stopped. Before allowing others to use the bot, run:

```sh
docker compose build
docker compose run --rm bot python -m app.owner_id
```

The helper needs only `BOT_TOKEN`. It prints user IDs from private `/whoami` messages among the first 100 pending updates, without saving or printing other messages. Copy your ID into `OWNER_TELEGRAM_ID`. On EC2, prefix the Docker commands with `sudo`. With the local Python environment instead, load `.env` as in Development and run `.venv/bin/python -m app.owner_id`.

If there is a large backlog or an active webhook, the helper may not find the message. It does not clear pending updates or change webhooks. This lookup also requires working Telegram connectivity.

## Server setup

On an **Ubuntu 24.04** server, install Docker and Git:

```sh
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker
```

Follow [Clone and configure](#2-clone-and-configure) above. To edit `.env` in the terminal, run `nano .env`; save with Ctrl+O, Enter, then Ctrl+X.

Stop any local copy using the same token. From the project directory, check time synchronization and start:

```sh
timedatectl status
sudo docker compose up -d --build
sudo docker compose ps
```

| Check | Expected result |
| --- | --- |
| Host clock | `System clock synchronized: yes` |
| Container | `Up` |
| Telegram | Bot responds to `/start` |

The container keeps running after SSH disconnects and restarts after a server reboot unless intentionally stopped. Stop gracefully with `sudo docker compose down`.

**[AWS EC2 deployment guide →](docs/EC2.md)** — instance settings, SSH access, updates, and troubleshooting.

## Usage

Only the configured owner is authorized initially. Other people open the bot, send `/start`, and press **Request access**. The owner receives the request in their private chat with **Approve** and **Deny** buttons. The owner must press Start in the bot first so it can deliver requests; failed delivery leaves the request available through `/requests`.

Unapproved people can check their own ID with `/whoami`, view access status, and request access. Their secret submissions are not parsed or used, and deletion is attempted. They should wait for approval before sending secrets. Denied or revoked users cannot submit another request; the owner can restore access using `/approve`.

| Action | Result |
| --- | --- |
| `/start` or `/help` | Instructions and privacy information |
| `/whoami` | Your own numeric Telegram user ID; available before approval |
| `/generate` | Request a secret; the prompt expires after one minute |
| Send a Base32 secret or `otpauth://totp/...` link | Start a five-minute session |
| **Refresh** | Update the code and timers without extending the session |
| **Stop** or `/cancel` | End the session and attempt message cleanup |

A new valid secret replaces only that user's session. The bot attempts to delete secret messages immediately and session messages when finished.

Owner commands work only from `OWNER_TELEGRAM_ID` in its private chat:

| Owner action | Result |
| --- | --- |
| `/requests` | Pending requests, 20 per page, with Review and Approve/Deny buttons |
| `/users` | Approved users, 20 per page; the owner is always allowed |
| `/approve 123456789` | Grant or restore access to that numeric user ID |
| `/revoke 123456789` | Remove access, end the user's active session, and attempt message cleanup |

Decisions take effect without restarting. Old request buttons cannot override a later decision. The owner cannot revoke their own access. If the owner command menu is unavailable on first startup, commands still work when typed; restarting after the owner presses Start registers the menu.

<details>
<summary><b>Supported secrets and code settings</b></summary>

Raw secrets default to **six digits, SHA-1, and a 30-second period**.

TOTP links support:

- SHA-1, SHA-256, or SHA-512.
- Six or eight digits.
- A 30- or 60-second period.

HOTP, QR images, and authenticator export links are not supported.

</details>

## Privacy and operation

- **Deletion has limits.** Outages and crashes can prevent cleanup; screenshots and notifications may remain.
- **Restarts end sessions.** Secrets and message IDs are not restored. Old messages may need manual deletion, and updates queued during downtime are discarded at startup.
- **Approvals persist.** SQLite stores numeric user IDs, access status, request identifiers, and timestamps in `data/access.sqlite3` locally. The supplied Compose setup keeps it in the named `access-data` volume, writable by the container's non-root user. It stores no TOTP secrets, codes, display names, or message contents. Each bot ID has separate records; a different bot starts with only its configured owner authorized.
- **Keep the authorization volume.** Normal rebuilds, restarts, and `docker compose down` retain access records. `docker compose down -v` deletes them. Local Python and Docker use separate storage unless explicitly configured to share it. Storage errors prevent startup or deny the affected request; they never enable public access.
- **Protect the host.** Keep its clock synchronized, swap disabled, and recovery codes stored separately. Python does not guarantee forensic memory erasure.
- **Payloads stay out of logs.** Application logs exclude secrets, codes, and message payloads. The supplied Compose configuration disables persistent container logs.

Stop with `docker compose down`. Allow the configured **30-second shutdown period** for message cleanup. Test with a dummy secret before using real accounts.

## Development

<details>
<summary><b>Run locally with Python and uv</b></summary>

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/). Create `.env` as described in [Local setup](#local-setup), including `OWNER_TELEGRAM_ID`, then run:

```sh
uv sync --locked --extra dev
set -a
. ./.env
set +a
uv run --locked --extra dev python -m app.main
```

Run checks:

```sh
uv run --locked --extra dev pytest
uv run --locked --extra dev ruff check app tests
```

</details>
