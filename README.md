# G-FLO v33 — storefront + Python admin backend

The storefront is the same single-file site as v32, but the catalogue no longer lives in
the HTML. Products, prices, stock, photos, categories, brands and contact details now sit
in a database that you edit through a password-protected admin website. Firebase and the
fake "Continue with Google" / on-screen OTP sign-in are gone.

```
gflo-v33/
├── backend/            FastAPI app + admin website (Python)
│   ├── app/            main.py, api.py, admin.py, models.py, security.py, store.py
│   │   ├── templates/  the admin pages (Jinja2)
│   │   └── static/     admin.css
│   ├── scripts/        bootstrap.py, seed.py, import_pricelist.py, create_admin.py,
│   │                   export_snapshot.py
│   ├── data/           gflo.db (896 products, pre-seeded), seed data, photos
│   └── requirements.txt
├── site/               the storefront — gflo.html plus src/ sections and build.sh
├── Dockerfile          builds backend + site into one image
├── PRICE-LIST-NOTES.md what came out of the 2026 price list + the 78 rates still blank
├── render.yaml         one-click Render blueprint
└── northflank.md       Northflank setup steps
```

## Run it locally

```sh
cd backend
pip install -r requirements.txt
python scripts/create_admin.py gflo 'your-long-password'   # pick your own login
./run.sh                                                   # http://localhost:8000
```

The catalogue is already in `backend/data/gflo.db` (896 products), so there's nothing to
import on a first local run. No admin account ships with it — the command above creates
yours, and the password is stored only as a hash. To rebuild the database from scratch:

```sh
rm backend/data/gflo.db && python scripts/seed.py
```

* Store: <http://localhost:8000/>
* Admin: <http://localhost:8000/admin/login>
* API docs: <http://localhost:8000/api/docs>

## Deploy

Both platforms run the included `Dockerfile`. The one thing that matters is a
**persistent volume mounted at `/data`** — without it, the database and every uploaded
photo are wiped on the next redeploy.

* **Render** — commit `render.yaml`, then New → Blueprint. Set `ADMIN_PASSWORD` in the
  dashboard. A persistent disk needs a paid instance; the free tier also sleeps after
  15 minutes idle, so a first visitor waits ~30s.
* **Northflank** — see `northflank.md`.

On first boot the container seeds the catalogue and creates the admin user from
`ADMIN_USERNAME` / `ADMIN_PASSWORD`. Look for `[bootstrap] created admin` in the logs.

## The admin website

The console is a proper seller dashboard: fixed sidebar with live counts, global product search
(press `/`), spreadsheet-style inline editing with toasts, a floating bulk-action bar, sticky save
bars that warn about unsaved changes, drag-and-drop photo upload, and a responsive layout that
collapses to a drawer on a phone. Styling lives in `app/static/admin.css`, behaviour in
`app/static/admin.js`, icons are rendered server-side from `ICON_PATHS` in `app/admin.py` — no
icon font, no CSS framework, no build step.

| Page | What you do there |
|---|---|
| `/admin/login` | Username + password. Nothing on the public site links here. |
| `/admin` | Counts: live, hidden, out of stock, no price, no photo, plus recent changes. |
| `/admin/products` | The main screen. Search, filter and see active filters as removable chips. **Edit price, MRP and stock straight in the table** — click a cell, type, Enter saves (Esc undoes, arrow keys walk down the column) and a toast confirms. Tick rows and a bulk bar rises from the bottom: change price by % or ₹, set stock, set to price-on-request, show/hide, move category, delete. |
| `/admin/products/new` | Full product form — name, code, category, price, MRP, stock, unit, size, packing, colours, material, warranty, weight, description, badges, visibility. |
| `…/edit` | Same form plus **photo upload** (drag and drop, multiple files, pick the main one, remove). |
| `/admin/categories` | Add, rename, reorder, set the tile image. Deleting is blocked while products still use it. |
| `/admin/brands` | Same for brands. |
| `/admin/import` | Export the whole catalogue as CSV, edit in Excel, upload it back. Also documents re-running the price-list import. |
| `/admin/settings` | Contact details, the **show prices / enquiry-only switch**, your password, and extra admin accounts. |
| `/admin/activity` | Every sign-in (including failures) and every catalogue change. |

Changes save immediately and appear on the storefront the next time a visitor loads a
page. You never rebuild or re-upload `gflo.html` again.

## How the storefront gets its data

At boot the page calls `GET /api/catalog` and builds its product database from the
response. If the API can't be reached — opened from `file://`, backend restarting — it
silently falls back to the catalogue still baked into `gflo.html`, so the store never
renders empty. The browser console says which one is in use.

Hosting the HTML somewhere else (a CDN, Shopify page, another domain)? Set
`window.GFLO_API_BASE = "https://your-api-host"` before the scripts run and add that
origin to CORS.

### Offline / static copy

```sh
python scripts/export_snapshot.py        # -> site/gflo-snapshot.html
```

Bakes the current catalogue and every uploaded photo into one HTML file that browses
with no backend at all — handy for sharing a preview or hosting the shop on a static
host. A snapshot is frozen, so re-run it after catalogue edits.

## What changed in the storefront

* Fake Google / OTP customer sign-in **removed**; there are no customer accounts. Cart,
  wishlist and enquiries still work, stored in the visitor's own browser.
* The demo "Admin console" with invented marketplace data is **gone**; `#/admin` now
  redirects to the real staff sign-in.
* Prices, categories, contact details and the show-prices switch come from the admin
  panel. `CATALOG_MODE` is no longer edited in code.
* Products with no rate show **"Price on request"** instead of ₹0, and are excluded from
  cart totals — the cart reports them as quote items.
* Per-unit pricing is shown where it matters ("per 1000 pcs", "per packet", "per coil").
* Editing the storefront's look still works the old way: edit `site/src/*.html`, run
  `./build.sh`, restart nothing — the server serves the file from disk.

## Security notes

* Passwords are stored as **scrypt** hashes with a per-user salt; plain text is never
  written anywhere.
* Sessions are HMAC-signed, timestamped, HTTP-only cookies (12 hours by default), marked
  Secure automatically when served over HTTPS.
* Login attempts are rate-limited per IP and per username (8 tries, then a 15-minute
  lockout) and every attempt is logged.
* Uploads are restricted to image extensions, capped at 8 MB, stored under random
  filenames, and downscaled to 1600 px.
* The admin site sends `noindex, nofollow` and is not linked from the storefront.
* Set a long random `SECRET_KEY` in production so sessions survive restarts predictably.

## Error monitoring with Sentry (optional)

Set `SENTRY_DSN` and every unhandled 500 — with its stack trace, the URL and the
signed-in admin username — lands in your Sentry project instead of scrolling past
in the Render logs. Leave it unset and monitoring stays off; nothing else changes.

```sh
SENTRY_DSN='https://...@o0.ingest.sentry.io/0' SENTRY_ENVIRONMENT=production ./run.sh
```

Look for `[gflo] Sentry monitoring on (environment=production)` in the boot log.

**Nothing sensitive is sent.** `send_default_pii` is off, and `app/monitoring.py`
scrubs every event before it leaves the process: passwords (including the
change-password fields), the `gflo_admin` session cookie, the `gflo_csrf` token,
`Authorization` headers and any `api_key`/`token` extras are replaced with
`[redacted]`. Only the username is attached, never an email or IP.

| Variable | Default | What it does |
|---|---|---|
| `SENTRY_DSN` | *(unset)* | Enables monitoring. Treat as a secret. |
| `SENTRY_ENVIRONMENT` | `development` | Tag shown in Sentry. Use `production` live. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` | Performance tracing (billed). `0.1` = 10% of requests. |
| `SENTRY_RELEASE` | *(unset)* | Version/commit tag for events. |

Healthy traffic sends nothing — only actual errors, so a free Sentry plan is
plenty for a shop this size.

## Backups

SQLite is a single file. To back up, copy it:

```sh
cp /data/gflo.db ~/gflo-backup-$(date +%F).db     # or download it from the host
tar czf gflo-media.tgz /data/media                # uploaded photos
```

Restoring is copying the file back and restarting.

## Re-running the price list

`backend/data/pl_products.json` holds the 196 products transcribed from
*G-FLO Price List 2026*. When the printed list changes, edit that file and run:

```sh
python scripts/import_pricelist.py            # add new / refresh existing rows
python scripts/import_pricelist.py --prices   # only update rates
```

Both match on the G-FLO code, update in place, and never touch products you created by
hand in the panel.
