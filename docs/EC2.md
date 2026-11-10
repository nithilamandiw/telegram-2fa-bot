# AWS EC2 Deployment

Run one bot container on Ubuntu 24.04 LTS using Docker Compose. No web server, domain, database, or load balancer is required.

## 1. Launch and connect

[Launch an EC2 instance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html) with:

| Setting | Value |
| --- | --- |
| Image | Ubuntu Server 24.04 LTS, x86_64 |
| Instance | `t3.micro` for light use; adjust for workload |
| Storage | 10 GiB encrypted gp3 |
| Network | Public subnet with internet access and public IPv4 enabled |
| Inbound rule | SSH, TCP 22, from your public IPv4 address followed by `/32` |

Keep outbound access available for updates, time synchronization, and Telegram. Inbound HTTP/HTTPS ports are unnecessary. Review regional compute, storage, and public IPv4 costs before launching.

Connect from your computer, replacing the key path and address:

```sh
chmod 400 /path/to/ec2-key.pem
ssh -i /path/to/ec2-key.pem ubuntu@EC2_PUBLIC_IP
```

Verify the host fingerprint using the EC2 console before accepting the first connection. Run all remaining commands on EC2.

## 2. Install Docker and clone from GitHub

```sh
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker

git clone https://github.com/nithilamandiw/telegram-2fa-bot.git
cd telegram-2fa-bot
```

These are Ubuntu 24.04 packages. If apt cannot locate them, enable Universe with `sudo add-apt-repository universe`, refresh the package index, and retry. The public repository can be cloned without GitHub credentials.

## 3. Configure and start

Create `.env` for this deployment:

```sh
umask 077
cp .env.example .env
nano .env
```

Set `BOT_TOKEN` to your BotFather token. Optionally change `DISPLAY_TIMEZONE` to an IANA timezone. Save and exit the editor. Keep `.env` private and out of Git.

In BotFather, disable group joining (`/setjoingroups`) and inline mode (`/setinline` → `/empty`). Stop any other copy using the same bot token.

Check the host clock and swap:

```sh
timedatectl status
swapon --show
```

The clock must be synchronized and swap disabled. Start the service:

```sh
sudo docker compose up -d --build
sudo docker compose ps
```

The service should show `Up`. Test `/start`, code refresh, Stop, and five-minute expiry in Telegram using a dummy secret first.

You can close SSH after startup. Docker and the container restart automatically after an instance reboot unless the container was intentionally stopped. Active sessions end on restart; message deletion is best effort.

## 4. Update, stop, and troubleshoot

Run these commands from `~/telegram-2fa-bot`.

Update from GitHub and rebuild:

```sh
git pull --ff-only origin main
sudo docker compose up -d --build
```

The ignored `.env` stays on the server. Updates restart the bot and end active sessions.

Stop gracefully:

```sh
sudo docker compose down
```

For startup errors, run the bot in the foreground after stopping the detached service:

```sh
sudo docker compose run --rm bot
```

Check the token, BotFather settings, outbound connectivity, and whether another bot instance is running. Container log storage is disabled, so use foreground output for diagnostics. Press Ctrl+C before restarting with `sudo docker compose up -d`.

Before retiring the server, stop the bot gracefully. Terminate unused instances and review retained volumes and allocated IP addresses for ongoing charges. See the [README](../README.md#privacy-and-operation) for privacy and cleanup limits.
