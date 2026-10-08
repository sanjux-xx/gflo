"""Coupons — Admin -> Coupons through to the cart check and the order.

Run against a SCRATCH database:
  DATA_DIR=/tmp/cpdata SITE_DIR=/path/to/site SECRET_KEY=... ADMIN_USERNAME=gflo \
  ADMIN_PASSWORD=PosterTestPass123 ORDER_RATE_LIMIT=200 COUPON_CHECK_LIMIT=100 uvicorn app.main:app --port 9850 --no-proxy-headers
  python tests/coupon_flows.py [B=http://127.0.0.1:9850]
"""
import datetime as dt
import re, sys
import httpx

args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
B = args.get("B", "http://127.0.0.1:9850")
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


def page():
    return c.get("/admin/coupons").text


def cid(code):
    m = re.search(r'<span class="cp-code">%s</span>.*?/admin/coupons/(\d+)/edit' % re.escape(code), page(), re.S)
    return m.group(1) if m else None


def new(**f):
    d = {"kind": "pct", "active": "on"}; d.update(f)
    return post("/admin/coupons/new", d)


CART = [{"sku": "CP-1000", "qty": 2}]          # ₹2,000
_ph = [9100000000]


def phone():
    _ph[0] += 1
    return str(_ph[0])


def check(code, items=CART, ph=""):
    return anon.post("/api/coupons/check", json={"code": code, "items": items, "phone": ph}).json()


def order(code, items=CART, ph=None):
    return anon.post("/api/orders", json={"name": "Coupon Tester", "phone": ph or phone(), "address": "12 MG Road",
                                          "city": "Hyderabad", "state": "Telangana", "pincode": "500080", "coupon": code, "items": items})


today = (dt.datetime.utcnow() + dt.timedelta(minutes=330)).date()
post("/admin/categories/save", {"name": "Coupon Test", "id": "coupon-test"})
post("/admin/products/new", {"sku": "CP-1000", "name": "Thousand Part", "category_id": "coupon-test",
                             "price": "1000", "stock": "50", "unit": "piece", "visible": "on"})
post("/admin/products/new", {"sku": "CP-300", "name": "Small Part", "category_id": "coupon-test",
                             "price": "300", "stock": "50", "unit": "piece", "visible": "on"})

print("=" * 70)
print("coupons: access control")
r = anon.get("/admin/coupons")
rec("signed-out visitor is sent to login", r.status_code == 303 and "/admin/login" in r.headers.get("location", ""))
r = anon.post("/admin/coupons/new", data={"code": "HACK", "kind": "flat", "value": "999"})
rec("signed-out create refused", r.status_code in (303, 403))
r = c.post("/admin/coupons/new", data={"code": "NOCSRF", "kind": "flat", "value": "50", "active": "on"})
rec("create without CSRF refused (403)", r.status_code == 403)
r = c.post("/admin/coupons/master", data={"state": "on"})
rec("master switch without CSRF refused (403)", r.status_code == 403)

print("coupons: defaults after install")
p = page()
rec("old coupons kept as examples", all(x in p for x in ("GFLO10", "FLAT100", "FREESHIP")))
rec("examples are switched off", p.count("Switch on") >= 3)
rec("coupons start OFF", "Turn coupons ON" in p)
rec("catalogue tells the shop coupons are off", httpx.get(B + "/api/catalog").json().get("couponsEnabled") is False)
rec("no coupon codes in the website code", all(x not in httpx.get(B + "/").text for x in ("GFLO10", "FLAT100", "FREESHIP")))
d = check("GFLO10")
rec("check while OFF: not available", not d.get("ok") and "aren't available" in d.get("error", ""), str(d))

print("coupons: master ON")
r = post("/admin/coupons/master", {"state": "on"})
rec("master ON", "Coupons are ON" in r.text and httpx.get(B + "/api/catalog").json().get("couponsEnabled") is True)
d = check("GFLO10")
rec("example still off -> invalid", not d.get("ok") and "isn't a valid" in d.get("error", ""), str(d))

print("coupons: create")
r = new(code="diwali10", value="10", max_discount="150", min_order="999", note="Diwali offer")
rec("create % coupon (code made upper-case)", "Coupon DIWALI10 created" in r.text, r.text[-400:])
d = check("diwali10")
rec("10% of ₹2,000 capped at ₹150", d.get("ok") and d.get("discount") == 150 and d.get("code") == "DIWALI10", str(d))
rec("customer sees a readable description", "10% off (max ₹150)" in d.get("label", ""), d.get("label"))
d = check("DIWALI10", [{"sku": "CP-300", "qty": 1}])
rec("below minimum -> clear message", not d.get("ok") and "minimum order of ₹999" in d.get("error", ""), str(d))
r = order("DIWALI10"); o = r.json()
rec("order gets the same discount", o.get("discount") == 150 and o.get("total") == 1949, str(o))
new(code="FLAT500", kind="flat", value="500")
d = check("FLAT500", [{"sku": "CP-300", "qty": 1}])
rec("₹ off never more than the items cost", d.get("discount") == 300, str(d))
new(code="SHIPFREE", kind="ship")
o = order("SHIPFREE", [{"sku": "CP-300", "qty": 1}]).json()
rec("free-delivery coupon removes the ₹99", o.get("shipping") == 0 and o.get("total") == 300, str(o))
o = anon.post("/api/orders", json={"name": "Coupon Tester", "phone": phone(), "address": "12 MG Road", "city": "Hyderabad", "state": "telangana",
                                   "pincode": "500080", "coupon": "SHIPFREE", "ship": "install",
                                   "items": [{"sku": "CP-300", "qty": 1}]}).json()
rec("...but install charge still applies", o.get("shipping") == 249, str(o))

print("coupons: validation in the admin")
for f, word in [({"code": "A B!"}, "letters or numbers"), ({"code": "X"}, "letters or numbers"),
                ({"code": "DIWALI10", "value": "5"}, "already exists"), ({"code": "PCT150", "value": "150"}, "between 1 and 100"),
                ({"code": "FLAT0", "kind": "flat", "value": "0"}, "how many rupees"), ({"code": "NEGMIN", "value": "5", "min_order": "-1"}, "negative"),
                ({"code": "TXT", "value": "ten"}, "must be a number"),
                ({"code": "BADDATES", "value": "5", "starts_on": "2026-12-10", "ends_on": "2026-12-01"}, "end date is before"),
                ({"code": "BADDATE", "value": "5", "starts_on": "31-31-2026"}, "valid date")]:
    r = new(**{"value": "5", **f})
    rec(f"refuses: {word}", word in r.text.replace("&#39;", "'"), r.text[-300:])
rec("refused coupons were not saved", all(cid(x) is None for x in ("PCT150", "FLAT0", "NEGMIN", "TXT", "BADDATES", "BADDATE")))

print("coupons: dates, limits, one per number")
new(code="LATER", value="5", starts_on=(today + dt.timedelta(days=5)).isoformat())
d = check("LATER"); rec("future coupon -> 'starts on'", not d.get("ok") and "starts on" in d.get("error", ""), str(d))
rec("admin shows it as Scheduled", "Scheduled" in page())
new(code="OLD", value="5", starts_on=(today - dt.timedelta(days=5)).isoformat(), ends_on=(today - dt.timedelta(days=1)).isoformat())
d = check("OLD"); rec("ended coupon -> 'expired'", not d.get("ok") and "expired" in d.get("error", ""), str(d))
new(code="TODAY", value="5", starts_on=today.isoformat(), ends_on=today.isoformat())
rec("coupon running today only works", check("TODAY").get("ok"))
new(code="FIRST2", kind="flat", value="50", usage_limit="2")
o1 = order("FIRST2").json(); o2 = order("FIRST2").json()
rec("usage limit 2: first two orders get it", o1.get("discount") == 50 and o2.get("discount") == 50, str((o1, o2)))
d = check("FIRST2"); rec("third customer: 'fully used'", not d.get("ok") and "fully used" in d.get("error", ""), str(d))
r = order("FIRST2"); rec("third order refused (not silently charged more)", r.status_code == 409 and r.json().get("coupon_error"), r.text)
rec("admin shows 2 / 2 and Used up", "2 / 2" in page() and "Used up" in page())
oid = re.search(r'/admin/orders/(\d+)">\s*<b class="mono">%s<' % o1["number"], c.get("/admin/orders").text).group(1)
post(f"/admin/orders/{oid}/status", {"status": "cancelled"})
rec("cancelling an order frees its coupon use", check("FIRST2").get("ok"))
new(code="ONCE", kind="flat", value="40", once_per_phone="on")
ph = phone()
rec("one-per-number: first use works", order("ONCE", ph=ph).json().get("discount") == 40)
d = check("ONCE", ph=ph); rec("same number again: refused", not d.get("ok") and "already been used" in d.get("error", ""), str(d))
r = order("ONCE", ph=ph); rec("same number order: refused", r.status_code == 409)
rec("another number: works", order("ONCE").json().get("discount") == 40)

print("coupons: edit / switch off / delete")
i = cid("DIWALI10")
r = post(f"/admin/coupons/{i}/edit", {"code": "DIWALI15", "kind": "pct", "value": "15", "min_order": "999", "active": "on"})
rec("edit + rename works", "Coupon DIWALI15 saved" in r.text and check("DIWALI15").get("discount") == 300, r.text[-300:])
rec("old name stops working", not check("DIWALI10").get("ok"))
r = post(f"/admin/coupons/{i}/edit", {"code": "FLAT500", "kind": "pct", "value": "15", "active": "on"})
rec("can't rename onto another coupon's code", "already exists" in r.text and cid("DIWALI15") == i)
post(f"/admin/coupons/{i}/toggle")
rec("switched off -> invalid", not check("DIWALI15").get("ok"))
post(f"/admin/coupons/{i}/toggle")
rec("switched on -> works again", check("DIWALI15").get("ok"))
post(f"/admin/coupons/{cid('FLAT500')}/delete")
rec("deleted coupon stops working", not check("FLAT500").get("ok") and cid("FLAT500") is None)
od = c.get(f"/admin/orders/{oid}").text
rec("past orders keep their coupon and discount", "FIRST2" in od and "−₹50" in od)
r = post("/admin/coupons/master", {"state": "off"})
rec("master OFF: every code stops", "Coupons are OFF" in r.text and not check("DIWALI15").get("ok"))
rec("master OFF: shop hides the coupon box", httpx.get(B + "/api/catalog").json().get("couponsEnabled") is False)
post("/admin/coupons/master", {"state": "on"})

print("coupons: safety")
new(code="XSSNOTE", value="5", note="<script>alert(1)</script>")
p = page()
rec("note is escaped in the admin", "<script>alert(1)</script>" not in p and "&lt;script&gt;" in p)
d = check("<img src=x onerror=alert(1)>")
rec("odd code text is refused safely", not d.get("ok"), str(d))
r = anon.post("/api/coupons/check", content=b'{"code":"DIWALI15"}', headers={"content-type": "text/plain"})
rec("check needs JSON (blocks cross-site forms)", r.status_code == 415)
d = check("DIWALI15", [{"sku": "CP-1000", "qty": 2, "price": 1}])
rec("check uses server prices, not the browser's", d.get("discount") == 300, str(d))
rec("changes are in the activity log", "coupon" in c.get("/admin/activity").text)
codes = [anon.post("/api/coupons/check", json={"code": f"GUESS{n}", "items": CART}).status_code for n in range(110)]
rec("guessing codes is slowed down (429)", 429 in codes, str(codes[-5:]))

print("=" * 70)
print(f"{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
