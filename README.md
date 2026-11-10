<div align="center">

<h1>🔐 Telegram 2FA Bot</h1>

<p>Temporary authentication codes, right in Telegram.</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.12 or newer">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&amp;logo=docker&amp;logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Session-5_minutes-7C3AED?style=flat-square" alt="Five-minute sessions">
  <img src="https://img.shields.io/badge/Storage-Memory_only-475569?style=flat-square" alt="Sessions stored in memory only">
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

Send a TOTP secret in a private chat. Get a code that refreshes automatically for **five minutes**, with a countdown showing when the code changes and when the session ends.

| ⏱️ Temporary sessions | 👤 Separate for each user |
| --- | --- |
| Five-minute lifetime with an early **Stop** button. | Each user can view and control only their own session. |
| **📊 Live countdowns** | **🧠 Memory-only state** |
| Code and session time bars update about every five seconds. | No database, saved accounts, or session backups. |

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
umask 077
cp .env.example .env
```

Edit `.env` and set your BotFather token:

```dotenv
BOT_TOKEN=your_botfather_token
DISPLAY_TIMEZONE=Asia/Colombo
```

`DISPLAY_TIMEZONE` accepts an IANA timezone. Keep `.env` out of Git and use it only for bot configuration, never TOTP secrets.

### 3. Start

```sh
docker compose up -d --build
docker compose ps
```

When the service shows **Up**, open your bot in Telegram and send `/start`. Run **one instance per bot token**.

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

| Action | Result |
| --- | --- |
| `/start` or `/help` | Instructions and privacy information |
| `/generate` | Request a secret; the prompt expires after one minute |
| Send a Base32 secret or `otpauth://totp/...` link | Start a five-minute session |
| **Refresh** | Update the code and timers without extending the session |
| **Stop** or `/cancel` | End the session and attempt message cleanup |

A new valid secret replaces only that user's session. The bot attempts to delete secret messages immediately and session messages when finished.

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
- **Protect the host.** Keep its clock synchronized, swap disabled, and recovery codes stored separately. Python does not guarantee forensic memory erasure.
- **Payloads stay out of logs.** Application logs exclude secrets, codes, and message payloads. The supplied Compose configuration disables persistent container logs.

Stop with `docker compose down`. Allow the configured **30-second shutdown period** for message cleanup. Test with a dummy secret before using real accounts.

## Development

<details>
<summary><b>Run locally with Python and uv</b></summary>

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/). Create `.env` as described in [Local setup](#local-setup), then run:

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
