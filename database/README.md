# database/ — empty by design (clean start)

- No `.db`, no `tables.xlsx` in this repo (gitignored).
- Runtime creates `atlas_bot.db` (SQLite) locally via `config/config.yaml`.
- Ticket-002 migrates to Postgres (`DATABASE_URL`), adds `site_id` everywhere.
- Master contractor/zone data is reseeded per site, never committed.
