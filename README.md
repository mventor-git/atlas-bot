# Contractor-Bot

> Telegram-based construction daily labor tracker with period contractor reports. Clean rebrand of Labor-Report, fresh start with zero loaded data.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://core.telegram.org/bots)

---

## Stack (V1 vs V2)

- **V1 (current): Microsoft Excel** - `openpyxl` template fill + `pywin32` Excel COM pixel-perfect PDF (Windows-only). Example templates in `templates/`, cell map in `config/config.yaml`. Templates are sacred: fill cells only.
- **V2 (planned): LibreOffice** - headless convert replaces Excel COM for cross-platform PDF. Excel stays as fallback.
- **DB:** V1 SQLite local (`database/contractor_bot.db`, gitignored) -> Postgres migration (multi-site isolation). `DATABASE_URL` in `.env`.
- **Bot:** `python-telegram-bot` v21, Telegram-only UI. Python 3.12+.

## Multi-site (V1)

Single bot, isolated per site (`site_id` on every row). Day-1 sites: `15 - Nady ElShams`, `00 - Administration (HQ)`. No cross-site views.

| Role | Perms (own site) |
| Site Engineer | view/edit/create/report/approve |
| Site Manager | view + create labor report only |
| Project Manager | full; new PM via full-employee confirm -> guest view-only |
| Dev (superadmin) | full + Atlas agent control |
| Guest | view-only |

## Quick Start

```bash
git clone https://github.com/mventor-git/contractor-bot.git
cd contractor-bot
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

- Services: `bot` (python:3.12-slim) + `db` (postgres:16-alpine, data in `pgdata` volume).
- Limit: V1 PDF-via-Excel COM needs Windows + Excel, so PDF export runs on host only. Everything else (reports, Excel fill via openpyxl, Telegram) works in the container. The LibreOffice ticket removes this gap.

## Project Structure

```
contractor-bot/
├── app/            # bot/config/database/excel/models/pdf/repositories/services/utils/workflow
├── config/         # config.yaml (cell map) + egypt_holidays.json
├── templates/      # sacred Excel templates (small/medium/large/empty-day/contractor_report)
├── database/       # empty at clone (gitignored .db); Postgres target
├── exports/        # generated pdf/excel/preview (gitignored)
├── logs/           # app logs (gitignored)
├── scripts/        # maintenance utilities
├── tests/          # pytest suite
└── main.py
```

Private folders (`tickets/`, `profile.md`, `.muse`, `.mventor`, vision data) stay local via `.gitignore`.

## Docs

- `profile.md` (local) - project identity, full spec
- `docs/PROJECT_STATE.md` - current status
- `docs/HANDOVER.md` - session continuation
- Legacy frozen at `D:\Projects\stable\Labor-Report` (read-only reference, never modify).

## Brand

Atlas. This public repo is company-free opensource. The Opal Construction fork (created after the V1 last ticket) applies the Atlas mini-logo + footer `Atlas Powered by Opal - Working Project <SITE>` on every doc it produces.

## License

MIT - see LICENSE.
