# Usage

## Setup

1. Prereqs: Linux, `python3` 3.11+, LibreOffice + `python3-uno` (`apt install libreoffice-calc python3-uno`).
2. `git clone --recursive <repo>` (or `git submodule update --init`). `cp template.ods spreadsheet.ods`.
3. Venv (Debian python is PEP 668 externally managed): `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
4. Discord: https://discord.com/developers/applications -> New Application -> Bot -> Reset Token (this is `DISCORD_TOKEN`). Under Bot -> Privileged Gateway Intents enable **Message Content Intent**. OAuth2 -> URL Generator: scope `bot`, permissions View Channels, Send Messages, Attach Files, Read Message History; open the URL to add the bot to a server (DMs need a shared server).
5. User ids: Discord -> Settings -> Advanced -> Developer Mode, right-click user -> Copy User ID.
6. `cp .env.example .env`, fill it in. The bot loads `.env` at the repo root automatically (stdlib parser: `KEY=value`, optional `export `, optional quotes, `#` comments); real environment variables take precedence over it. A missing `.env` is fine.

## Env vars

| var | req | default | notes |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | | bot token |
| `ANTHROPIC_API_KEY` | yes | | read by the SDK |
| `ALLOWED_USER_IDS` | yes | | comma-separated; everyone else is ignored silently |
| `SPREADSHEET_PATH` | no | `spreadsheet.ods` | relative paths resolve against the repo root; must exist |
| `MODEL` | no | `claude-opus-5-5` | |
| `EFFORT` | no | `medium` | `low\|medium\|high\|xhigh\|max` |
| `CHANNEL_IDS` | no | | comma-separated channels where allowed users get replies without mentioning the bot |
| `HISTORY_IDLE_MINUTES` | no | `30` | a channel's conversation is wiped after this much inactivity |
| `HISTORY_MAX_CHARS` | no | `400000` | safety ceiling (about 100k tokens); over it, history is wiped and the reply says so |
| `SHEET_PYTHON` | no | `/usr/bin/python3` | interpreter for the sheet CLI and skill scripts |

`SHEET_PYTHON` is the system python because `python3-uno` is installed there; the venv cannot import `uno`. The bot process itself never imports `uno` (sheet.py runs as a subprocess), so the venv needs no system-site-packages.

Startup fails with a message on missing/invalid config, missing spreadsheet, or missing `claude-sheets` submodule.

## Run

```
cd src && ../.venv/bin/python -m finance_bot
```

Logs go to stderr (tool calls, API errors, approvals).

## Talking to it

Allowed users: DM the bot, mention it in a server, or write in a channel listed in `CHANNEL_IDS`. Conversation history is per channel, in memory (lost on restart), wiped after `HISTORY_IDLE_MINUTES` idle or by `!reset`. Long replies are split at 2000 chars.

## Approval flow

- Skill creation/update: the bot posts the proposal (SKILL.md and scripts as attachments; for updates, a unified diff) with Approve/Reject buttons. Files are written to `skills/<name>/` only on Approve. Updates overwrite only the files included; nothing is deleted.
- Script runs: every `run_script` call is posted (skill/script, args) and needs Approve first.
- Only `ALLOWED_USER_IDS` can click. No click for 10 minutes = rejected.
- Scripts run via `SHEET_PYTHON`, cwd = repo root, 60 s timeout, with a minimal env (PATH, HOME, locale, `SPREADSHEET_PATH`, `SHEET_PYTHON`, `SHEET_CLI`); the Discord and Anthropic keys are not passed.
- Spreadsheet edits made through the `sheet` tool are not gated; ask the bot to `--backup` or keep your own copies.

## Homelab (systemd)

```
# /etc/systemd/system/finance-bot.service
[Unit]
Description=finance-bot
After=network-online.target
Wants=network-online.target

[Service]
User=finance
WorkingDirectory=/opt/finance-bot/src
EnvironmentFile=/etc/finance-bot.env
ExecStart=/opt/finance-bot/.venv/bin/python -m finance_bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`/etc/finance-bot.env`: `KEY=value` lines, mode 600; it overrides the repo `.env`. Alternatively drop `EnvironmentFile` and rely on `.env` in the repo (keep it mode 600). The service user needs a writable `HOME` (LibreOffice profile) and write access to the repo (`spreadsheet.ods`, `skills/`, `claude-sheets/.sheetcli/`). `systemctl enable --now finance-bot`; `journalctl -u finance-bot -f`.
