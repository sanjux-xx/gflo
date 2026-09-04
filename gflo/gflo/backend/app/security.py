"""Admin authentication — self-hosted, no Firebase, no third-party OAuth.

Passwords: hashlib.scrypt (stdlib) with a per-user random salt.
Sessions:  HMAC-signed, timestamped, HTTP-only cookie (stdlib hmac) carrying a
           per-user token_version so sessions can be REVOKED server-side.
CSRF:      double-submit cookie; every unsafe /admin request must echo it back.
Brute force: per-IP + per-username attempt counter with a lockout window, using
           a client IP that is only taken from X-Forwarded-For behind a trusted proxy.
"""
import base64, hashlib, hmac, ipaddress, json, os, secrets, time
from typing import Optional
from fastapi import Request

SESSION_COOKIE = "gflo_admin"
CSRF_COOKIE = "gflo_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", 60 * 60 * 12))   # 12 hours
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", 8))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 900))

# Force the Secure cookie flag regardless of forwarded headers. Set this in any
# HTTPS deployment instead of relying on a proxy sending x-forwarded-proto.
COOKIE_SECURE_ALWAYS = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
# Comma separated list of proxy addresses/CIDRs whose X-Forwarded-For we trust.
TRUSTED_PROXIES = [p.strip() for p in os.environ.get("TRUSTED_PROXIES", "").split(",") if p.strip()]

_SECRET_FILE_NAME = "session_secret"

# A real scrypt hash of a random throwaway password. Verified against when a
# username does not exist so the response time matches the "user exists" path
# and usernames cannot be enumerated by timing (L-05).
_DUMMY_HASH: Optional[str] = None


def _secret() -> bytes:
    """Secret for cookie signing. From SECRET_KEY env, else persisted in DATA_DIR."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode()
    from .db import DATA_DIR
    path = os.path.join(DATA_DIR, _SECRET_FILE_NAME)
    if os.path.exists(path):
        return open(path, "rb").read().strip()
    val = secrets.token_hex(32).encode()
    with open(path, "wb") as fh:
        fh.write(val)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return val


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(dk_b64)))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def dummy_verify(password: str) -> bool:
    """Burn the same scrypt work as a real check, for unknown usernames."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    verify_password(password or "", _DUMMY_HASH)
    return False


def password_problem(password: str) -> Optional[str]:
    if len(password or "") < 10:
        return "Password must be at least 10 characters."
    if password.lower() in {"password12", "1234567890", "adminadmin"}:
        return "That password is too easy to guess."
    return None


# ------------------------------------------------------------------- sessions
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def make_session(username: str, token_version: int = 0) -> str:
    payload = _b64(json.dumps({"u": username, "t": int(time.time()),
                               "v": int(token_version or 0)}).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def read_session_claims(token: str) -> Optional[dict]:
    """Validate signature + age and return the raw claims ({u, t, v})."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expect = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        data = json.loads(_unb64(payload))
    except Exception:
        return None
    if int(time.time()) - int(data.get("t", 0)) > SESSION_MAX_AGE:
        return None
    if not data.get("u"):
        return None
    return data


def read_session(token: str) -> Optional[str]:
    """Signature/age only. Prefer current_user(), which also checks the DB."""
    claims = read_session_claims(token)
    return claims.get("u") if claims else None


def current_user(request: Request) -> Optional[str]:
    """Resolve the signed-in admin, confirming the account still exists and that
    the token has not been revoked (logout / password change / deletion)."""
    claims = read_session_claims(request.cookies.get(SESSION_COOKIE, ""))
    if not claims:
        return None
    username = claims.get("u")
    user = _lookup_user(request, username)
    if user is None:                                  # deleted / unknown account
        return None
    if int(claims.get("v", -1)) != int(user.token_version or 0):
        return None                                   # revoked session
    return username


def current_admin(request: Request):
    """The AdminUser row for the signed-in admin, or None."""
    claims = read_session_claims(request.cookies.get(SESSION_COOKIE, ""))
    if not claims:
        return None
    user = _lookup_user(request, claims.get("u"))
    if user is None or int(claims.get("v", -1)) != int(user.token_version or 0):
        return None
    return user


def _lookup_user(request: Request, username: Optional[str]):
    """Fetch the AdminUser, caching on request.state for repeat calls."""
    if not username:
        return None
    cached = getattr(request.state, "_admin_row", None)
    if cached is not None and cached[0] == username:
        return cached[1]
    from sqlalchemy import func
    from .db import SessionLocal
    from .models import AdminUser
    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(func.lower(AdminUser.username) == username.lower()).first()
        if user is not None:
            db.expunge(user)                       # usable after the session closes
    finally:
        db.close()
    try:
        request.state._admin_row = (username, user)
    except Exception:
        pass
    return user


def is_elevated(user) -> bool:
    """Owner/admin may manage accounts, settings and bulk import/export."""
    if user is None:
        return False
    return bool(getattr(user, "is_owner", False)) or (getattr(user, "role", "") or "") in ("owner", "admin")


def cookie_kwargs(request: Request) -> dict:
    secure = (COOKIE_SECURE_ALWAYS or request.url.scheme == "https"
              or request.headers.get("x-forwarded-proto") == "https")
    return {"httponly": True, "samesite": "strict", "secure": secure,
            "max_age": SESSION_MAX_AGE, "path": "/"}


# ----------------------------------------------------------------------- CSRF
def csrf_cookie_kwargs(request: Request) -> dict:
    kw = cookie_kwargs(request)
    kw["httponly"] = False          # the inline-edit fetch reads it
    return kw


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(request: Request, submitted: Optional[str]) -> bool:
    """Double-submit check: the form field / header must match the cookie."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not submitted:
        return False
    return hmac.compare_digest(str(submitted), cookie)


# --------------------------------------------------------------- brute force
_attempts: dict = {}


def _prune(key: str):
    entries = [t for t in _attempts.get(key, []) if time.time() - t < LOGIN_LOCKOUT_SECONDS]
    if entries:
        _attempts[key] = entries
    else:
        _attempts.pop(key, None)


def login_blocked(keys) -> int:
    """Returns seconds remaining in lockout, or 0 when allowed."""
    worst = 0
    for key in keys:
        _prune(key)
        entries = _attempts.get(key, [])
        if len(entries) >= LOGIN_MAX_ATTEMPTS:
            worst = max(worst, int(LOGIN_LOCKOUT_SECONDS - (time.time() - min(entries))))
    return max(worst, 0)


def record_failure(keys):
    for key in keys:
        _attempts.setdefault(key, []).append(time.time())


def clear_failures(keys):
    for key in keys:
        _attempts.pop(key, None)


def _proxy_trusted(peer: Optional[str]) -> bool:
    if not peer or not TRUSTED_PROXIES:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in TRUSTED_PROXIES:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """The peer address. X-Forwarded-For is honoured ONLY when the direct peer is
    a configured trusted proxy, otherwise any client could spoof its identity and
    walk straight through the per-IP rate limiter (M-03)."""
    peer = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for")
    if fwd and _proxy_trusted(peer):
        # rightmost entry that is not itself a trusted proxy = real client
        for candidate in reversed([p.strip() for p in fwd.split(",") if p.strip()]):
            if not _proxy_trusted(candidate):
                return candidate
    return peer or "unknown"
