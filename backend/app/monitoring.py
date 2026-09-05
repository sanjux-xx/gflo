"""Sentry error monitoring — optional, off unless SENTRY_DSN is set.

Why this file exists: a crash on the live shop is otherwise invisible unless
someone happens to be watching the Render logs. With a DSN configured, every
unhandled 500 (and its stack trace) lands in Sentry with the URL, the admin
username when signed in, and the release tag.

Privacy: send_default_pii stays False and _scrub() strips anything that could
carry a secret — passwords, the session and CSRF cookies, Authorization
headers, and the CSRF form field. Sentry never receives a credential.
"""
import os
from typing import Optional

DSN = (os.environ.get("SENTRY_DSN") or "").strip()
ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "development")
RELEASE = (os.environ.get("SENTRY_RELEASE") or "").strip() or None

# Performance traces are billed separately, so default to off.
try:
    TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0)
except ValueError:
    TRACES_SAMPLE_RATE = 0.0

# Field / header / cookie names whose values must never be sent.
_SENSITIVE_KEYS = {
    "password", "current", "new", "confirm", "csrf_token", "admin_password",
    "secret_key", "sentry_dsn", "token", "api_key", "authorization",
}
_SENSITIVE_COOKIES = {"gflo_admin", "gflo_csrf"}
_REDACTED = "[redacted]"


def configured() -> bool:
    return bool(DSN)


def _scrub_mapping(data):
    if not isinstance(data, dict):
        return data
    out = {}
    for key, value in data.items():
        if str(key).lower() in _SENSITIVE_KEYS:
            out[key] = _REDACTED
        elif isinstance(value, dict):
            out[key] = _scrub_mapping(value)
        else:
            out[key] = value
    return out


def _scrub(event, hint):
    """Last line of defence before an event leaves the process."""
    try:
        request = event.get("request") or {}

        # form / query payloads
        if isinstance(request.get("data"), dict):
            request["data"] = _scrub_mapping(request["data"])

        # headers: drop auth + cookies entirely
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                low = name.lower()
                if low in ("authorization", "cookie", "set-cookie", "x-csrf-token"):
                    headers[name] = _REDACTED

        cookies = request.get("cookies")
        if isinstance(cookies, dict):
            for name in list(cookies):
                if name in _SENSITIVE_COOKIES:
                    cookies[name] = _REDACTED

        # never ship the query string of a login attempt
        if isinstance(request.get("query_string"), str) and "password" in request["query_string"]:
            request["query_string"] = _REDACTED

        if request:
            event["request"] = request

        # scrub any extra context the app attached
        if isinstance(event.get("extra"), dict):
            event["extra"] = _scrub_mapping(event["extra"])
    except Exception:
        # a scrubbing bug must never break error reporting or the request
        pass
    return event


def init() -> bool:
    """Start Sentry if a DSN is configured. Safe to call when it isn't."""
    if not DSN:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
    except ImportError:
        print("[gflo] SENTRY_DSN is set but sentry-sdk isn't installed — monitoring off")
        return False
    try:
        sentry_sdk.init(
            dsn=DSN,
            environment=ENVIRONMENT,
            release=RELEASE,
            traces_sample_rate=TRACES_SAMPLE_RATE,
            send_default_pii=False,          # no bodies/IPs/cookies by default
            max_request_body_size="small",
            before_send=_scrub,
            integrations=[StarletteIntegration(), FastApiIntegration()],
        )
    except Exception as exc:                 # a bad DSN must not stop the shop
        print(f"[gflo] Sentry init failed ({type(exc).__name__}) — monitoring off")
        return False
    print(f"[gflo] Sentry monitoring on (environment={ENVIRONMENT})")
    return True


def note_admin(username: Optional[str]):
    """Tag the current scope with the signed-in admin, so an error in the
    console says who hit it. No email or IP, just the username."""
    if not DSN or not username:
        return
    try:
        import sentry_sdk
        sentry_sdk.set_user({"username": username})
    except Exception:
        pass
