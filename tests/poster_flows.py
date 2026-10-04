"""Festival posters & banners — admin upload through to the public API.

Run against a SCRATCH copy of the database (this suite creates and deletes
posters and uploads junk files):

  DATA_DIR=/tmp/pdata SITE_DIR=/path/to/site SECRET_KEY=... \
  uvicorn app.main:app --host 127.0.0.1 --port 9200 --no-proxy-headers
  python tests/poster_flows.py

Set B, USER and PW below to match. Every check reads the result back from the
public API or the admin page, not just the HTTP status of the form post.
"""
import datetime as dt
import io, re, sys
import httpx
from PIL import Image

B = "http://127.0.0.1:9200"
USER, PW = "gflo", "PosterTestPass123"

checks = []


def rec(name, ok, detail=""):
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail and not ok else ""))


def img(w=1080, h=1350, colour=(236, 51, 56), fmt="JPEG"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, fmt)
    return buf.getvalue()


def api():
    return httpx.get(B + "/api/posters", timeout=30).json()


c = httpx.Client(base_url=B, follow_redirects=False, timeout=30)
anon = httpx.Client(base_url=B, follow_redirects=False, timeout=30)
tok = re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
r = c.post("/admin/login", data={"username": USER, "password": PW, "csrf_token": tok})
assert r.status_code == 303, f"login failed: HTTP {r.status_code}"


def post(url, data=None, files=None, csrf=True):
    d = dict(data or {})
    if csrf:
        d["csrf_token"] = c.cookies.get("gflo_csrf")
    return c.post(url, data=d, files=files, follow_redirects=True)


def poster_ids():
    return [int(x) for x in re.findall(r'/admin/posters/(\d+)/edit', c.get("/admin/posters").text)]


today = (dt.datetime.utcnow() + dt.timedelta(minutes=330)).date()
print("=" * 70)
print("posters: access control")
r = anon.get("/admin/posters")
rec("signed-out visitor is sent to login", r.status_code == 303 and "/admin/login" in r.headers.get("location", ""))
r = anon.post("/admin/posters/new", data={"title": "x"})
rec("signed-out POST is refused", r.status_code in (303, 403))
r = c.post("/admin/posters/new", data={"title": "NoCsrf", "active": "on"},
           files={"image": ("a.jpg", img(), "image/jpeg")})
rec("POST without CSRF token is refused (403)", r.status_code == 403)

print("posters: upload + public API")
before = set(poster_ids())
r = post("/admin/posters/new",
         {"title": "Diwali Sale Test", "style": "popup", "frequency": "daily",
          "link_url": "/categories", "button_text": "Shop the offer", "active": "on"},
         files={"image": ("diwali.jpg", img(), "image/jpeg"),
                "mobile_image": ("diwali-m.png", img(720, 1280, (20, 20, 120), "PNG"), "image/png")})
rec("upload succeeds", r.status_code == 200 and "uploaded" in r.text.lower(), r.text[:300])
new = sorted(set(poster_ids()) - before)
pid = new[-1] if new else None
rec("poster appears in the admin list", pid is not None)
d = api()
rec("API returns it as the live popup", bool(d.get("popup")) and d["popup"]["title"] == "Diwali Sale Test", str(d))
rec("API includes mobile image, link and button",
    d.get("popup") and d["popup"]["imgMobile"].startswith("/media/") and d["popup"]["link"] == "/categories"
    and d["popup"]["cta"] == "Shop the offer")
rec("uploaded image is served", d.get("popup") and httpx.get(B + d["popup"]["img"]).status_code == 200)
rec("admin page marks it 'On the site now'", "On the site now" in c.get("/admin/posters").text)

print("posters: validation")
for bad in ["javascript:alert(1)", "//evil.example", "data:text/html,hi", "/x\" onerror=\"y"]:
    r = post("/admin/posters/new", {"title": "BadLink", "link_url": bad, "active": "on"},
             files={"image": ("a.jpg", img(), "image/jpeg")})
    rec(f"rejects link {bad!r}", "BadLink" not in c.get("/admin/posters").text)
r = post("/admin/posters/new", {"title": "NotImage", "active": "on"},
         files={"image": ("evil.jpg", b"<html><script>alert(1)</script>", "image/jpeg")})
rec("rejects a non-image disguised as .jpg", "NotImage" not in c.get("/admin/posters").text)
r = post("/admin/posters/new", {"title": "BadDates", "active": "on",
                                "starts_on": "2026-11-05", "ends_on": "2026-11-01"},
         files={"image": ("a.jpg", img(), "image/jpeg")})
rec("rejects end date before start date", "end date is before" in r.text)
r = post("/admin/posters/new", {"title": "NoFile", "active": "on"})
rec("requires an image", "NoFile" not in c.get("/admin/posters").text)

print("posters: scheduling")
future = (today + dt.timedelta(days=10)).isoformat()
r = post("/admin/posters/new", {"title": "Future Strip", "style": "strip", "active": "on",
                                "starts_on": future},
         files={"image": ("s.jpg", img(1600, 200), "image/jpeg")})
rec("future poster is saved as Scheduled", "Scheduled" in c.get("/admin/posters").text)
rec("future poster is NOT on the API", api().get("strip") is None)
past_s = (today - dt.timedelta(days=10)).isoformat()
past_e = (today - dt.timedelta(days=1)).isoformat()
r = post("/admin/posters/new", {"title": "Old Strip", "style": "strip", "active": "on",
                                "starts_on": past_s, "ends_on": past_e},
         files={"image": ("s.jpg", img(1600, 200), "image/jpeg")})
rec("expired poster is NOT on the API", api().get("strip") is None)
r = post("/admin/posters/new", {"title": "Live Strip", "style": "strip", "active": "on",
                                "starts_on": today.isoformat(), "ends_on": today.isoformat()},
         files={"image": ("s.jpg", img(1600, 200), "image/jpeg")})
d = api()
rec("strip running today only IS on the API", d.get("strip") and d["strip"]["title"] == "Live Strip", str(d))
rec("popup and strip are independent", d.get("popup") and d["popup"]["title"] == "Diwali Sale Test")

print("posters: edit / toggle / delete")
v1 = api()["popup"]["v"]
r = post(f"/admin/posters/{pid}/edit", {"title": "Diwali Sale Edited", "style": "popup",
                                        "frequency": "once", "active": "on", "remove_mobile": "on"})
d = api()["popup"]
rec("edit keeps the image when no new file is chosen", d["img"].startswith("/media/"))
rec("edit changes title and frequency", d["title"] == "Diwali Sale Edited" and d["freq"] == "once")
rec("remove mobile version works", d["imgMobile"] == "")
rec("edited poster gets a new version key (shown again)", d["v"] != v1)
post(f"/admin/posters/{pid}/toggle")
rec("switching off removes it from the API", api().get("popup") is None)
post(f"/admin/posters/{pid}/toggle")
rec("switching on brings it back", api().get("popup") is not None)
print("posters: master ON / OFF switch")
r = post("/admin/posters/master", {"state": "off"})
rec("master OFF: API shows nothing", api() == {"popup": None, "strip": None}, str(api()))
rec("master OFF: admin page says OFF", "Turn posters ON" in r.text and "Paused" in r.text)
post(f"/admin/posters/{pid}/toggle"); post(f"/admin/posters/{pid}/toggle")
rec("master OFF wins over a poster switched on", api().get("popup") is None)
r = c.post("/admin/posters/master", data={"state": "on"})
rec("master switch without CSRF is refused", r.status_code == 403)
rec("...and it stays OFF", api().get("popup") is None)
r = post("/admin/posters/master", {"state": "on"})
rec("master ON: live posters come back", api().get("popup") is not None and api().get("strip") is not None)
rec("master switch is in the activity log", "master switch" in c.get("/admin/activity").text)
img_url = api()["popup"]["img"]
for i in poster_ids():
    if i in before:
        continue
    post(f"/admin/posters/{i}/delete")
rec("delete removes posters", not (set(poster_ids()) - before))
rec("delete removes the uploaded image file", httpx.get(B + img_url).status_code == 404)
rec("API is empty again", api() == {"popup": None, "strip": None})
rec("delete is recorded in the activity log", "poster" in c.get("/admin/activity").text)

print("storefront")
html = httpx.get(B + "/").text
rec("storefront loads poster.js and poster.css", "/static/poster.js" in html and "/static/poster.css" in html)
rec("poster.js is served", httpx.get(B + "/static/poster.js").status_code == 200)

print("=" * 70)
print(f"{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
