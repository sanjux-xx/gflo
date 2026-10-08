"""Storefront orders — checkout API through to the admin Orders pages.

Run against a SCRATCH database (creates products and orders):
  DATA_DIR=/tmp/odata SITE_DIR=/path/to/site SECRET_KEY=... ADMIN_USERNAME=gflo \
  ADMIN_PASSWORD=PosterTestPass123 ORDER_RATE_LIMIT=40 uvicorn app.main:app --port 9800 --no-proxy-headers
  python tests/order_flows.py [B=http://127.0.0.1:9800]

Checks: prices come from the database (never from the browser), sizes, coupons,
delivery charges, validation, stock/hidden products, rate limit, admin login
required, status changes, notes, the new-order feed and the sidebar badge.
"""
import re, sys
import httpx

args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
B = args.get("B", "http://127.0.0.1:9800")
USER, PW = "gflo", "PosterTestPass123"
checks = []


def rec(name, ok, detail=""):
    checks.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail and not ok else ""))


c = httpx.Client(base_url=B, follow_redirects=False, timeout=60)
anon = httpx.Client(base_url=B, follow_redirects=False, timeout=60)
tok = re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
assert c.post("/admin/login", data={"username": USER, "password": PW, "csrf_token": tok}).status_code == 303


def post(url, data=None):
    d = dict(data or {}); d["csrf_token"] = c.cookies.get("gflo_csrf")
    return c.post(url, data=d, follow_redirects=True)


_ph = [9000000000]


def order(body):
    """POST an order. Each call gets a fresh mobile number unless the test sets
    one, so the per-number limit only bites in the rate-limit tests. Start the
    server with ORDER_RATE_LIMIT=40 (per-connection cap) for this suite."""
    b = dict(body)
    if b.get("phone") == BASE["phone"]:
        _ph[0] += 1; b["phone"] = str(_ph[0])
    return anon.post("/api/orders", json=b)


BASE = {"name": "Ravi Kumar", "phone": "9876543210", "address": "12 MG Road, Gandhi Nagar",
        "city": "Hyderabad", "state": "Telangana", "pincode": "500080", "ship": "std"}

# ------------------------------------------------------------- test products
post("/admin/categories/save", {"name": "Order Test", "id": "order-test"})
prods = {
    "OT-PLAIN": {"name": "Plain Part", "price": "500", "stock": "10"},
    "OT-BIG": {"name": "Big Part", "price": "6000", "stock": "5"},
    "OT-SIZE": {"name": "Sized Screw", "price": "", "stock": "10",
                "variants": "20 x 10 mm = 850\n45 x 12 mm = 1850\nCustom = "},
    "OT-CLASH": {"name": "Clash Sizes", "price": "", "stock": "10",
                 "variants": "10 mm = 100\n10-mm = 200\n10 mm-2 = 300"},
    "OT-QUOTE": {"name": "Quote Part", "price": "", "stock": "10"},
    "OT-OOS": {"name": "Sold Out Part", "price": "300", "stock": "0"},
    "OT-HIDDEN": {"name": "Hidden Part", "price": "300", "stock": "10", "hidden": True},
}
for sku, p in prods.items():
    d = {"sku": sku, "name": p["name"], "category_id": "order-test", "price": p["price"],
         "stock": p["stock"], "unit": "piece", "variants": p.get("variants", "")}
    if not p.get("hidden"):
        d["visible"] = "on"
    post("/admin/products/new", d)
cat = {x["sku"]: x for x in httpx.get(B + "/api/catalog", timeout=60).json()["products"]}
rec("test products created", all(s in cat for s in prods if s != "OT-HIDDEN"), str(list(cat)[:8]))

print("=" * 70)
print("orders: access control")
r = anon.get("/admin/orders")
rec("signed-out visitor is sent to login", r.status_code == 303 and "/admin/login" in r.headers.get("location", ""))
r = anon.get("/admin/orders/feed")
rec("feed needs login (401)", r.status_code == 401)
r = anon.post("/admin/orders/1/status", data={"status": "shipped"})
rec("signed-out status change refused", r.status_code in (303, 403))

print("orders: server-side pricing")
r = order({**BASE, "items": [{"sku": "OT-PLAIN", "qty": 2, "price": 1}]})
d = r.json()
rec("order accepted", r.status_code == 200 and d.get("ok"), r.text[:300])
rec("order number looks like GF1001", re.fullmatch(r"GF\d{4,}", d.get("number", "")), d.get("number"))
rec("tampered browser price is ignored (2 x 500 = 1000)", d.get("subtotal") == 1000, str(d))
rec("delivery ₹99 below ₹4,999", d.get("shipping") == 99 and d.get("total") == 1099, str(d))
first = d.get("number")

r = order({**BASE, "items": [{"sku": "OT-BIG", "qty": 1}]}); d = r.json()
rec("free delivery from ₹4,999", d.get("shipping") == 0 and d.get("total") == 6000, str(d))
r = order({**BASE, "ship": "exp", "items": [{"sku": "OT-BIG", "qty": 1}]}); d = r.json()
rec("express adds ₹99", d.get("shipping") == 99 and d.get("total") == 6099, str(d))
r = order({**BASE, "ship": "install", "items": [{"sku": "OT-PLAIN", "qty": 1}]}); d = r.json()
rec("install adds ₹249 on top of ₹99", d.get("shipping") == 348 and d.get("total") == 848, str(d))
r = order({**BASE, "ship": "free-please", "items": [{"sku": "OT-PLAIN", "qty": 1}]}); d = r.json()
rec("unknown delivery option falls back to standard", d.get("shipping") == 99, str(d))

print("orders: sizes")
r = order({**BASE, "items": [{"sku": "OT-SIZE~45-x-12-mm", "qty": 2}, {"sku": "OT-SIZE~20-x-10-mm", "qty": 1}]})
d = r.json()
rec("size prices come from the size list (2x1850 + 850)", d.get("subtotal") == 4550, str(d))
rec("size label saved on the order", sorted(i["size"] for i in d.get("items", [])) == ["20 x 10 mm", "45 x 12 mm"], str(d))
r = order({**BASE, "items": [{"sku": "OT-SIZE", "qty": 1}]})
rec("sized product without a size is refused", r.status_code == 409, r.text)
r = order({**BASE, "items": [{"sku": "OT-SIZE~99-mm", "qty": 1}]})
rec("unknown size is refused", r.status_code == 409, r.text)
r = order({**BASE, "items": [{"sku": "OT-SIZE~custom", "qty": 1}, {"sku": "OT-PLAIN", "qty": 1}]}); d = r.json()
rec("size with no price = on request", d.get("quote_items") == 1 and d.get("subtotal") == 500, str(d))
r = order({**BASE, "items": [{"sku": "OT-CLASH~10-mm", "qty": 1}, {"sku": "OT-CLASH~10-mm-2", "qty": 1},
                             {"sku": "OT-CLASH~10-mm-2-2", "qty": 1}]}); d = r.json()
rec("look-alike size names map to the right price (same codes as the shop)",
    [(i["size"], i["price"]) for i in d.get("items", [])] == [("10 mm", 100), ("10-mm", 200), ("10 mm-2", 300)], str(d))
r = order({**BASE, "items": [{"sku": "OT-QUOTE", "qty": 3}]}); d = r.json()
rec("product with no price = on request", r.status_code == 200 and d.get("quote_items") == 1 and d.get("subtotal") == 0, str(d))
quote_no = d.get("number")

print("orders: coupons")
# coupons are managed in Admin -> Coupons: switch the master on and the seeded examples on
post("/admin/coupons/master", {"state": "on"})
for cid in re.findall(r'/admin/coupons/(\d+)/toggle', c.get("/admin/coupons").text):
    post(f"/admin/coupons/{cid}/toggle")
r = order({**BASE, "coupon": "gflo10", "items": [{"sku": "OT-PLAIN", "qty": 2}]}); d = r.json()
rec("GFLO10 = 10% off (min ₹999)", d.get("discount") == 100 and d.get("total") == 999, str(d))
r = order({**BASE, "coupon": "GFLO10", "items": [{"sku": "OT-PLAIN", "qty": 1}]}); d = r.json()
rec("GFLO10 below minimum is refused with the reason", r.status_code == 409 and "minimum order" in d.get("error", ""), str(d))
r = order({**BASE, "coupon": "FLAT100", "items": [{"sku": "OT-PLAIN", "qty": 2}]}); d = r.json()
rec("FLAT100 = ₹100 off", d.get("discount") == 100, str(d))
r = order({**BASE, "coupon": "FREE1000", "items": [{"sku": "OT-PLAIN", "qty": 2}]}); d = r.json()
rec("made-up coupon is refused with a clear message", r.status_code == 409 and d.get("coupon_error"), str(d))
r = order({**BASE, "coupon": "FREESHIP", "items": [{"sku": "OT-PLAIN", "qty": 1}]}); d = r.json()
rec("FREESHIP = free standard delivery", d.get("shipping") == 0 and d.get("total") == 500, str(d))
post("/admin/coupons/master", {"state": "off"})
r = order({**BASE, "coupon": "GFLO10", "items": [{"sku": "OT-PLAIN", "qty": 2}]})
rec("coupons OFF in admin: code refused", r.status_code == 409, r.text)

print("orders: validation")
bad = [({"name": ""}, "name"), ({"phone": "12345"}, "mobile"), ({"phone": "1234567890"}, "mobile"),
       ({"address": "x"}, "address"), ({"pincode": "5000"}, "pincode"),
       ({"city": "12345"}, "city"), ({"city": "x"}, "city"), ({"state": ""}, "state"), ({"state": "Narnia"}, "state")]
for patch, word in bad:
    r = order({**BASE, **patch, "items": [{"sku": "OT-PLAIN", "qty": 1}]})
    rec(f"rejects bad {word}", r.status_code == 400 and word in r.json().get("error", "").lower(), r.text)
for good_city in ("नोएडा", "சென்னை", "Gurugram Sector 14", "St. Thomas Mount"):
    r = order({**BASE, "city": good_city, "items": [{"sku": "OT-PLAIN", "qty": 1}]})
    rec(f"accepts city {good_city!r}", r.status_code == 200, r.text)
r = order({**BASE, "items": []})
rec("empty cart refused", r.status_code == 400)
r = order({**BASE, "items": [{"sku": "OT-OOS", "qty": 1}]})
rec("out-of-stock product refused", r.status_code == 409 and "out of stock" in r.text, r.text)
r = order({**BASE, "items": [{"sku": "OT-HIDDEN", "qty": 1}]})
rec("hidden product refused", r.status_code == 409, r.text)
r = order({**BASE, "items": [{"sku": "NOPE-123", "qty": 1}]})
rec("unknown product refused", r.status_code == 409, r.text)
r = order({**BASE, "items": [{"sku": "OT-PLAIN", "qty": 5000}]}); d = r.json()
rec("more than in stock refused with how many are left", r.status_code == 409 and "Only 10" in d.get("error", ""), str(d)[:200])
r = order({**BASE, "items": [{"sku": "OT-PLAIN", "qty": -5}]})
rec("negative quantity refused", r.status_code == 400, r.text)
r = anon.post("/api/orders", content=b'{"name":"x"}', headers={"content-type": "text/plain"})
rec("non-JSON content type refused (blocks cross-site forms)", r.status_code == 415)
r = anon.post("/api/orders", content=b"not json", headers={"content-type": "application/json"})
rec("garbage body refused", r.status_code == 400)
r = order({**BASE, "name": "<script>alert(1)</script>", "items": [{"sku": "OT-PLAIN", "qty": 1}]})
xss_no = r.json().get("number")

print("orders: admin")
page = c.get("/admin/orders").text
rec("orders list shows the first order", first in page)
rec("new orders are flagged NEW", "NEW" in page)
feed = c.get("/admin/orders/feed").json()
rec("feed reports unseen orders", feed.get("unseen", 0) >= 10 and feed["latest"]["number"], str(feed))
unseen_before = feed["unseen"]
rec("sidebar badge shows unseen count", re.search(r'id="ord-badge"\s*>\s*%d<' % unseen_before, page) is not None)
oid = int(re.search(r'/admin/orders/(\d+)">\s*<b class="mono">%s<' % first, page).group(1))
det = c.get(f"/admin/orders/{oid}").text
rec("order shows city and state", "Hyderabad, Telangana" in det)
rec("order page shows customer, items and total",
    "Ravi Kumar" in det and "Plain Part" in det and "₹1,099" in det and "500080" in det, det[:200])
rec("order page has call / WhatsApp / print", re.search(r"tel:\+91\d{10}", det) and re.search(r"wa\.me/91\d{10}", det) and "data-print" in det)
rec("opening an order marks it seen", c.get("/admin/orders/feed").json()["unseen"] == unseen_before - 1)
r = c.post(f"/admin/orders/{oid}/status", data={"status": "shipped"})
rec("status change without CSRF refused", r.status_code == 403)
r = post(f"/admin/orders/{oid}/status", {"status": "shipped"})
rec("status change works", "marked Shipped" in r.text)
r = post(f"/admin/orders/{oid}/status", {"status": "hacked"})
rec("unknown status refused", "Unknown status" in r.text)
rec("filter by status", first in c.get("/admin/orders?status=shipped").text
    and first not in c.get("/admin/orders?status=new").text)
post(f"/admin/orders/{oid}/note", {"note": "Called, dispatch tomorrow"})
rec("staff note saved", "Called, dispatch tomorrow" in c.get(f"/admin/orders/{oid}").text)
qid = re.search(r'/admin/orders/(\d+)">\s*<b class="mono">%s<' % quote_no, c.get("/admin/orders").text).group(1)
rec("price-on-request lines shown as 'On request'", "On request" in c.get(f"/admin/orders/{qid}").text)
xid = re.search(r'/admin/orders/(\d+)">\s*<b class="mono">%s<' % xss_no, c.get("/admin/orders").text).group(1)
xp = c.get(f"/admin/orders/{xid}").text
rec("customer text is escaped (no script injection)", "<script>alert(1)</script>" not in xp and "&lt;script&gt;" in xp)
rec("missing order handled", "doesn" in c.get("/admin/orders/999999", follow_redirects=True).text)
rec("orders in activity log", "order" in c.get("/admin/activity").text)
rec("notifier script is served", httpx.get(B + "/static/orders-notify.js").status_code == 200
    and "orders-notify.js" in page)

print("orders: customer can see the status")
r = order({**BASE, "phone": "9822222222", "items": [{"sku": "OT-PLAIN", "qty": 1}]}); tn = r.json()["number"]
t = anon.get(f"/api/orders/{tn}/status", params={"phone": "9822222222"}).json()
rec("new order: status 'new' with placed time", t.get("status") == "new" and t.get("times", {}).get("new"), str(t))
rec("wrong mobile number can't see the order", anon.get(f"/api/orders/{tn}/status", params={"phone": "9833333333"}).status_code == 404)
rec("no mobile number can't see the order", anon.get(f"/api/orders/{tn}/status").status_code == 404)
tid = re.search(r'/admin/orders/(\d+)">\s*<b class="mono">%s<' % tn, c.get("/admin/orders").text).group(1)
for st in ("confirmed", "shipped", "delivered"):
    post(f"/admin/orders/{tid}/status", {"status": st})
t = anon.get(f"/api/orders/{tn}/status", params={"phone": "+91 98222 22222"}).json()
rec("after Delivered in admin, customer sees 'delivered'", t.get("status") == "delivered" and t.get("label") == "Delivered", str(t))
rec("each step's time is recorded", all(t.get("times", {}).get(k) for k in ("new", "confirmed", "shipped", "delivered")), str(t))
rec("admin order page lists the status history", "Delivered:" in c.get(f"/admin/orders/{tid}").text)

print("orders: track by mobile number")
lk = anon.get("/api/orders/lookup", params={"phone": "9822222222"}).json()
rec("lookup finds the customer's order", lk.get("ok") and any(o["number"] == tn for o in lk.get("orders", [])), str(lk)[:300])
one = next((o for o in lk.get("orders", []) if o["number"] == tn), {})
rec("lookup shows status, items and total", one.get("status") == "delivered" and one.get("items") and one.get("total"), str(one))
rec("lookup never shows name or address", "Ravi" not in str(lk) and "MG Road" not in str(lk) and "500080" not in str(lk), str(lk)[:300])
rec("unknown number: empty list", anon.get("/api/orders/lookup", params={"phone": "9700000001"}).json().get("orders") == [])
rec("bad number refused", anon.get("/api/orders/lookup", params={"phone": "12345"}).status_code == 400)
codes = [anon.get("/api/orders/lookup", params={"phone": "9822222222"}).status_code for _ in range(12)]
rec("one number can't be looked up endlessly (429)", 429 in codes, str(codes))
codes = [anon.get("/api/orders/lookup", params={"phone": str(9700000100 + n)}).status_code for n in range(25)]
rec("trawling many numbers is slowed down (429)", 429 in codes, str(codes))
rec("/track page opens", httpx.get(B + "/track").status_code == 200)

print("orders: rate limit")
same = {**BASE, "phone": "9811111111", "items": [{"sku": "OT-PLAIN", "qty": 1}]}
codes = [anon.post("/api/orders", json=same).status_code for _ in range(7)]
rec("one mobile number: 5 orders allowed, then slowed down (429)", codes[:5] == [200] * 5 and codes[5] == 429, str(codes))
codes = [order({**BASE, "items": [{"sku": "OT-PLAIN", "qty": 1}]}).status_code for _ in range(40)]
rec("one connection: generous cap, then slowed down (429)", codes[0] == 200 and 429 in codes, str(codes))

print("=" * 70)
print(f"{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
