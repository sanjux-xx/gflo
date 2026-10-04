# Deploying on Northflank

1. **Create a combined service** → *Build & Deploy from Git* → point it at this repo.
   Build type: **Dockerfile**, path `/Dockerfile`.
2. **Port**: add port `8000`, protocol HTTP, and enable *Publicly accessible*.
   (The container also honours `$PORT` if you set one.)
3. **Volume**: add a volume so the catalogue survives redeploys —
   * Mount path: `/data`
   * Size: 1 GB is plenty
4. **Environment variables** (Service → Environment):
   ```
   DATA_DIR=/data
   SITE_DIR=/site
   SECRET_KEY=<paste a random 64-char hex string>
   ADMIN_USERNAME=gflo
   ADMIN_PASSWORD=<your long admin password>
   COOKIE_SECURE=true
   SENTRY_DSN=<your Sentry DSN — optional>
   SENTRY_ENVIRONMENT=production
   ```
   Store `SECRET_KEY`, `ADMIN_PASSWORD` and `SENTRY_DSN` as **secrets**, not plain
   variables. `SENTRY_DSN` is optional: leave it out and error monitoring stays off.
5. **Deploy.** On first boot the container creates the database, loads the 896-product
   catalogue and creates your admin user. Watch the logs for
   `[bootstrap] created admin 'gflo'`.
6. Open `https://<your-service>.code.run/` for the store and `/admin/login` for the panel.

## FREE permanent storage (recommended): Northflank's free PostgreSQL addon
The free Developer Sandbox plan has no volumes, but it includes 1 free database.
Everything the admin saves — products, categories, posters, settings AND every
uploaded photo — is stored in that database, so redeploys and restarts lose nothing.

0. Deploy this code FIRST (older code has no Postgres driver and won't start).
1. Project → **Addons → Create addon → PostgreSQL** (free plan) → Create.
2. Open the addon → **Connection details** → copy **POSTGRES_URI** (the internal one).
3. Your service → **Environment** → add  `DATABASE_URL` = the value you copied
   (paste it exactly; `postgres://` / `postgresql://` both work). Save → it redeploys.
4. First start on the empty database rebuilds the catalogue and creates the admin from
   `ADMIN_USERNAME` / `ADMIN_PASSWORD` — make sure those are set.
5. Open /admin — no red "Storage is NOT permanent" banner = you're safe.
   Then redo admin-only work once (CSV imports, photo uploads, posters).

No volume needed. Product photos bundled with the code are served from the repo.

## Using Northflank Postgres instead of SQLite
Add a Postgres addon, then set `DATABASE_URL` on the service to the addon's
connection string with the `postgresql+psycopg://` prefix, and add `psycopg[binary]`
to `backend/requirements.txt`. Uploaded photos still need the `/data` volume.

## Notes
* Without a volume the database is wiped on every redeploy — the app will silently
  re-seed itself and you will lose admin edits. Attach the volume.
* Northflank's free/dev resources are enough for this store; scale to 0.5 vCPU /
  512 MB if pages feel slow.
