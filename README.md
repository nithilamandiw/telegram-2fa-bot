# Telegram 2FA Bot

Generate TOTP codes in a private Telegram chat for five minutes. Each user has an isolated session. Codes refresh automatically, with countdowns and time bars updated about every five seconds.

Secrets stay in memory. The bot attempts to delete secret messages immediately and session messages when the session ends. No database or session backups are used.

## Quick start

Requires Docker Engine and Docker Compose v2.

1. Create a bot with [@BotFather](https://t.me/BotFather) and obtain its token.
2. Disable group joining with `/setjoingroups` and inline mode with `/setinline` → `/empty`.
3. Clone the repository and create the configuration:

```sh
git clone https://github.com/nithilamandiw/telegram-2fa-bot.git
cd telegram-2fa-bot
umask 077
cp .env.example .env
```

Edit `.env` and set `BOT_TOKEN`. `DISPLAY_TIMEZONE` accepts an IANA timezone and defaults to `Asia/Colombo`. Never commit `.env` or put a TOTP secret in it.

Start the bot:

```sh
docker compose up -d --build
docker compose ps
```

Open the bot in Telegram and send `/start`. Run only one instance per bot token. For server deployment, see [AWS EC2 setup](docs/EC2.md).

## Usage

| Action | Result |
| --- | --- |
| `/start` or `/help` | Show instructions and privacy information |
| `/generate` | Request a secret; the prompt expires after one minute |
| Send a Base32 secret or `otpauth://totp/...` link | Start a five-minute session |
| **Refresh** | Update the current code and timers without extending the session |
| **Stop** or `/cancel` | End the session and attempt message cleanup |

A new valid secret replaces only that user's session. Raw secrets default to six digits, SHA-1, and a 30-second period. TOTP links support SHA-1/256/512, 6 or 8 digits, and 30- or 60-second periods. HOTP, QR images, and authenticator export links are not supported.

## Privacy and operation

- Telegram bot chats are not end-to-end encrypted. Telegram and the running bot process submitted secrets.
- Message deletion is best effort. Outages and crashes can prevent cleanup; screenshots and notifications may remain.
- Restarting ends all sessions. Secrets and message IDs are not restored, and old messages may need manual deletion. Updates queued during downtime are discarded on startup.
- Keep the host clock synchronized, swap disabled, and recovery codes stored separately. Python does not guarantee forensic memory erasure.
- Application logs exclude secrets, codes, and message payloads. The supplied Compose configuration disables persistent container logs.

To stop gracefully, run `docker compose down`. Allow the configured 30-second shutdown period for message cleanup. Test with a dummy secret before using real accounts.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Create `.env` as above, then run:

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
