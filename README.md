# Atlas-Bot

> Telegram-based construction daily labor tracker with period contractor reports. Opensource, zero loaded data at clone.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://core.telegram.org/bots)

---

## Stack

- **Docs: LibreOffice-native** - `.ots` templates filled with `odfpy`, PDFs rendered by headless `soffice`. Templates live in `templates/`, cell map in `config/config.yaml`. Templates are sacred: fill cells only. System requirement: LibreOffice Still + `soffice` on PATH.
- **DB:** SQLite local (`database/atlas_bot.db`, gitignored) -> Postgres (multi-site isolation). `DATABASE_URL` in `.env`.
- **Bot:** `python-telegram-bot` v21, Telegram-only UI. Python 3.12+.

## Multi-site

Single bot, isolated per site (`site_id` on every row). Ships with two demo sites out of the box - rename them in `config.yaml` for your deployment. No cross-site views.

| Role | Perms (own site) |
| Site Engineer | view/edit/create/report/approve |
| Site Manager | view + create labor report only |
| Project Manager | full; new PM via full-employee confirm -> guest view-only |
| Dev (superadmin) | full + Atlas agent control |
| Guest | view-only |

## Quick Start

```bash
git clone https://github.com/mventor-git/atlas-bot.git
cd atlas-bot
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # add BOT_TOKEN
python main.py --health
python main.py
```

## Docker

```bash
cp .env.example .env   # set BOT_TOKEN + POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f bot
```

- Services: `bot` (python:3.12-slim + LibreOffice Writer) + `db` (postgres:16-alpine, data in `pgdata` volume).
- PDF runs via headless `soffice`, identical on host and in the container.

## Project Structure

```
atlas-bot/
├── app/            # bot/config/database/libre/models/pdf/repositories/services/utils
├── config/         # config.yaml (cell map) + egypt_holidays.json
├── templates/      # sacred .ots templates (small/medium/large/empty-day/contractor_report)
├── database/       # tables.ods skeleton tracked; .db gitignored; Postgres target
├── exports/        # generated pdf/docs/preview (gitignored)
├── logs/           # app logs (gitignored)
├── scripts/        # maintenance utilities
├── tests/          # pytest suite
└── main.py
```

## Docs

This README is the full public spec. AI working files (`tickets/`, `docs/`, `profile.md`) are gitignored by design and never committed.

## Brand

Atlas-Bot. This public repo is company-free opensource. Forks apply their own mini-logo + footer (`Atlas Powered by <Company> - Working Project <SITE>`) on produced docs.

## License

MIT - see LICENSE.
