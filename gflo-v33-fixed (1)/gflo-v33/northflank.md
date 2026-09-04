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
   SERPAPI_KEY=<your SerpAPI key — optional>
   ```
   Store `SECRET_KEY`, `ADMIN_PASSWORD` and `SERPAPI_KEY` as **secrets**, not plain
   variables. `SERPAPI_KEY` is optional: leave it out and the SerpAPI features stay
   switched off. Every SerpAPI request is billed, so treat it like a password.
5. **Deploy.** On first boot the container creates the database, loads the 896-product
   catalogue and creates your admin user. Watch the logs for
   `[bootstrap] created admin 'gflo'`.
6. Open `https://<your-service>.code.run/` for the store and `/admin/login` for the panel.

## Using Northflank Postgres instead of SQLite
Add a Postgres addon, then set `DATABASE_URL` on the service to the addon's
connection string with the `postgresql+psycopg://` prefix, and add `psycopg[binary]`
to `backend/requirements.txt`. Uploaded photos still need the `/data` volume.

## Notes
* Without a volume the database is wiped on every redeploy — the app will silently
  re-seed itself and you will lose admin edits. Attach the volume.
* Northflank's free/dev resources are enough for this store; scale to 0.5 vCPU /
  512 MB if pages feel slow.
