# Security fixes applied to G-FLO v33

All 18 findings from the security assessment are fixed in this build. Verified by
re-running the original exploit paths (39/39 pass) plus a functional regression
suite (61/61 pass) against a fresh instance seeded from `backend/data/gflo.db`.

## High

**H-01 — Infinity/NaN price took the storefront API offline.**
Price/quantity inputs were parsed with an unbounded `float()`, so a bulk
"increase by 1e308%" stored `inf`. `/api/catalog` then could not be serialised
and returned HTTP 500 to every visitor. Added `parse_money()`, `parse_qty()` and
`clamp_money()` in `app/store.py` (finite, non-negative, capped) and wired them
through the product form, inline edit, bulk actions and CSV import.

**H-02 — Sessions could not be revoked.**
The session cookie was a signed `{user, timestamp}` blob that was never checked
against the database, so a stolen cookie survived logout, password change and
even account deletion. `AdminUser.token_version` is now part of the signed
payload and compared on every request; it is bumped on logout, password change
and password reset via `scripts/create_admin.py`. Deleted/unknown accounts are
rejected outright.

## Medium

**M-01 — No CSRF protection.** Double-submit token: a `gflo_csrf` cookie is
minted on admin GETs, embedded in all 18 POST forms and sent as `X-CSRF-Token`
by the inline-edit fetch. Enforced in middleware for every unsafe `/admin`
request.

**M-02 — Stored DOM XSS via category/brand names.** Category, brand and
part-type names were interpolated into `innerHTML` unescaped while product
fields were escaped. All 17 sinks now use `U.esc()`. Fixed in **both**
`site/gflo.html` **and** `site/src/*.html`, so re-running `./build.sh` cannot
reintroduce it.

**M-03 — Spoofable client IP defeated the login rate limiter.**
`X-Forwarded-For` is now honoured only when the direct peer is in
`TRUSTED_PROXIES`, and the rightmost untrusted hop is used. Note that
`uvicorn[standard]` also rewrites the client address from that header by
default, below the application layer — `run.sh` and the `Dockerfile` therefore
pass `--no-proxy-headers` unless `FORWARDED_ALLOW_IPS` is set explicitly.

**M-04 — Anonymous account-lockout DoS.** Mitigated by M-03 (attempts can no
longer be spread across spoofed IPs at zero cost) plus timing-equalised login.

**M-05 — Open redirect via `?next=`.** The GET login handler redirected to an
unvalidated `next` when already signed in. Both handlers now use `safe_next()`,
which only allows server-relative `/admin` paths.

**M-06 — Uploads validated by extension only.** Arbitrary bytes named `.jpg`
were stored and served as `image/jpeg`. Uploads must now actually decode as an
allowed image (`_probe_image()` + `verify()`), the detected format sets the
stored extension, and failures are rejected instead of silently swallowed.

**M-07 — Image decompression bomb.** The 8 MB byte cap did not bound decoded
size. Now capped at 40 MP / 12000 px per side, with `Image.MAX_IMAGE_PIXELS`
set so Pillow trips during decode too.

**M-08 — CSV import returned HTTP 500 on a bad cell.** Numeric conversions are
per-cell guarded and reported through the existing `problems[]` list.

**M-09 — Missing security headers.** Middleware adds `X-Content-Type-Options`,
`Referrer-Policy`, and on `/admin`: `X-Frame-Options: DENY`, a restrictive CSP
with `frame-ancestors 'none'`, and `Cache-Control: no-store`. HSTS is sent over
HTTPS.

**M-10 — Every admin was a superuser.** Added `AdminUser.role`
(`owner`/`admin`/`editor`). Account management, store settings and CSV
import/export now require owner or admin; editors keep full catalogue access.
Existing non-owner accounts migrate to `editor` (least privilege) and can be
promoted from the settings page.

## Low / Info

- **L-01** CSV export cells starting with `= + - @` are prefixed with `'`.
- **L-02** Negative prices and stock are rejected across all write paths.
- **L-03** Swagger UI and `/openapi.json` are off unless `ENABLE_DOCS=true`.
- **L-04** `/api/health` no longer reports catalogue counts.
- **L-05** Unknown usernames burn equivalent scrypt work (`dummy_verify`), so
  login timing no longer enumerates accounts.
- **L-06** Admin cookies are `SameSite=Strict`; set `COOKIE_SECURE=true` to force
  the Secure flag rather than inferring it from a proxy header.

## Also fixed during remediation

- The new `token_version` / `role` columns are applied by a shared
  `ensure_schema()` in `app/db.py`, called by the app **and** by
  `create_admin.py`, `bootstrap.py` and `seed.py` — otherwise the documented
  first-run (`python scripts/create_admin.py`) crashed on the shipped database.

## SerpAPI integration (added after the assessment)

`SERPAPI_KEY` is read from the environment only — never stored in the database,
never rendered into a page (the settings card shows a `abcd…9999` fingerprint,
not the key) and never logged. Verified: with a key set, the value appears 0
times in the server log, 0 times anywhere in the database and 0 times in the
audit log.

Because every SerpAPI request is billed, `POST /admin/serp/search` is gated on
three things — a valid session, the CSRF token, and an owner/admin role. An
unauthenticated or editor-level caller is refused, so a stranger cannot spend
your quota. Unset the variable and the whole feature is inert (HTTP 503).

## Deployment checklist

1. Set `SECRET_KEY` to a long random value.
2. Set `COOKIE_SECURE=true` when serving over HTTPS.
3. Leave `TRUSTED_PROXIES` / `FORWARDED_ALLOW_IPS` **unset** unless the app
   genuinely sits behind a reverse proxy; set both to that proxy if it does.
4. Leave `ENABLE_DOCS` unset in production.
5. Review admin roles after upgrading — pre-existing staff accounts are now
   `editor`.
6. Optional: set `SERPAPI_KEY` as a **secret** to enable Google catalogue
   research. Leave it unset to keep the feature off.
