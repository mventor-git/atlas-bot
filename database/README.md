# database/ - empty by design (clean start)

- `tables.ods` (tracked): master skeleton with headers only, no real data.
- Runtime creates `atlas_bot.db` (SQLite) locally via `config/config.yaml`.
- Postgres migration adds `site_id` everywhere (`DATABASE_URL`).
- Master contractor/zone data is reseeded per site, never committed.
