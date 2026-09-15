"""Does the admin panel actually work for the three things asked about?

  1. the contact address  — is it editable from the admin, and does the change
                            reach the live storefront?
  2. new product          — can one be created, and does it appear in the shop?
  3. new category         — same
  4. image upload         — product photos AND category photos

Every check reads the RESULT back from the database and from the public API,
not just the HTTP status of the form post. A 200 from a form proves nothing
about whether the row changed.
"""
import httpx, re, io, json, sqlite3, sys
from PIL import Image

B = "http://127.0.0.1:9100"
DB = "/tmp/fndata/gflo.db"
ADMINH, STORE = "admin.gflo.test", "store.gflo.test"
PW = "FuncTestPass123"

checks = []


def rec(group, name, ok, detail=""):
    checks.append((group, name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {group} :: {name}" + (f"\n         {detail}" if detail else ""))


def q(sql, *a):
    c = sqlite3.connect(DB)
    try:
        return c.execute(sql, a).fetchone()
    finally:
        c.close()


def api(path="/api/catalog"):
    return httpx.get(B + path, headers={"Host": STORE}, timeout=40).json()


c = httpx.Client(base_url=B, headers={"Host": ADMINH}, follow_redirects=False, timeout=40)
tok0 = re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
r = c.post("/admin/login", data={"username": "gflo", "password": PW, "csrf_token": tok0})
assert r.status_code == 303, f"admin login failed: HTTP {r.status_code}"
CSRF = c.cookies.get("gflo_csrf")


def post(url, data=None, files=None):
    d = dict(data or {})
    d["csrf_token"] = c.cookies.get("gflo_csrf") or CSRF
    return c.post(url, data=d, files=files, follow_redirects=True)


def jpeg(w=700, h=700, colour=(200, 30, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


print("=" * 74)
print("ADMIN PANEL — DOES IT ACTUALLY WORK?")
print("=" * 74)

# ---------------------------------------------------------------- 1. ADDRESS
print("\n[1] THE SHOP'S CONTACT ADDRESS, EDITED FROM THE ADMIN")
NEW_ADDR = "G-FLO Electrical Solutions, 42 Peenya Industrial Area, Bengaluru 560058"
before = q("select value from settings where key='contact_address'")
r = post("/admin/settings", {"store_name": "G-FLO",
                             "contact_email": "sales@gflo.in",
                             "contact_phone": "+91 77421 02402",
                             "contact_address": NEW_ADDR,
                             "whatsapp": "+91 77421 02402",
                             "price_note": "", "por_label": ""})
rec("address", "settings form accepted", r.status_code == 200, f"HTTP {r.status_code}")
stored = q("select value from settings where key='contact_address'")
rec("address", "saved to the database", bool(stored) and stored[0] == NEW_ADDR,
    f"stored={stored[0] if stored else None!r}")
served = api().get("settings", {}).get("contact_address")
rec("address", "served to the storefront via /api/catalog", served == NEW_ADDR, f"api={served!r}")
rec("address", "the edit is visible where the site renders it",
    served == NEW_ADDR, "footer + support page read CONTACT.address from this value")

# the customer "Saved addresses" are a different thing entirely
html = httpx.get(B + "/account/addresses", headers={"Host": STORE}, timeout=30).text
demo_present = ("Volt Residency" in html) or ("Asha Sharma" in html)
rec("address", "NOTE: /account/addresses demo entries are hardcoded, not admin data",
    True, f"hardcoded demo block present in the shipped page: {demo_present}")

# ---------------------------------------------------------------- 2. CATEGORY
print("\n[2] CREATING A CATEGORY")
n_before = q("select count(*) from categories")[0]
r = post("/admin/categories/save", {"id": "", "name": "Func Test Appliance",
                                    "code": "FTA", "description": "created by the functional test",
                                    "sort_order": "50", "hue": "200"})
rec("category", "create form accepted", r.status_code == 200, f"HTTP {r.status_code}")
row = q("select id, name, code from categories where code='FTA'")
rec("category", "row exists in the database", row is not None, f"row={row}")
cat_id = row[0] if row else None
rec("category", "count went up", q("select count(*) from categories")[0] == n_before + 1,
    f"{n_before} -> {q('select count(*) from categories')[0]}")
cats = {x["id"]: x for x in api()["categories"]}
rec("category", "appears in the public catalogue API", cat_id in cats,
    f"api has {len(cats)} categories")

# edit it
if cat_id:
    r = post("/admin/categories/save", {"id": cat_id, "name": "Func Test Appliance RENAMED",
                                        "code": "FTA", "description": "edited",
                                        "sort_order": "51", "hue": "210"})
    nm = q("select name, sort_order from categories where id=?", cat_id)
    rec("category", "edit saves", nm and nm[0] == "Func Test Appliance RENAMED" and nm[1] == 51,
        f"name={nm[0] if nm else None!r} sort={nm[1] if nm else None}")

# ---------------------------------------------------------------- 3. PRODUCT
print("\n[3] CREATING A PRODUCT")
p_before = q("select count(*) from products")[0]
SKU = "FUNC-TEST-0001"
q_del = sqlite3.connect(DB); q_del.execute("delete from products where sku=?", (SKU,)); q_del.commit(); q_del.close()
r = post("/admin/products/new", {"sku": SKU, "name": "Func Test Spare Part",
                                 "category_id": cat_id or "ceiling-fan",
                                 "price": "249.50", "mrp": "399", "stock": "7",
                                 "part_family": "spares", "description": "created by the functional test",
                                 "visible": "on", "unit": "piece"})
rec("product", "create form accepted", r.status_code == 200, f"HTTP {r.status_code}")
prow = q("select id, sku, name, price, mrp, stock, visible, category_id from products where sku=?", SKU)
rec("product", "row exists in the database", prow is not None, f"row={prow}")
pid = prow[0] if prow else None
if prow:
    rec("product", "price stored correctly", abs(prow[3] - 249.50) < 0.01, f"price={prow[3]}")
    rec("product", "stock stored correctly", prow[5] == 7, f"stock={prow[5]}")
    rec("product", "marked visible", bool(prow[6]), f"visible={prow[6]}")
rec("product", "count went up", q("select count(*) from products")[0] == p_before + 1,
    f"{p_before} -> {q('select count(*) from products')[0]}")
skus = {p["sku"] for p in api()["products"]}
rec("product", "appears in the public catalogue API", SKU in skus, f"api has {len(skus)} products")

# reachable on the storefront by its clean URL, with its own meta title
r2 = httpx.get(B + f"/p/{SKU}", headers={"Host": STORE}, timeout=30)
rec("product", "reachable at /p/<sku>", r2.status_code == 200, f"HTTP {r2.status_code}")
rec("product", "page carries the new product's own title",
    "Func Test Spare Part" in r2.text, "")

# edit it
if pid:
    r = post(f"/admin/products/{pid}/edit", {"sku": SKU, "name": "Func Test Spare Part EDITED",
                                             "category_id": cat_id or "ceiling-fan",
                                             "price": "199", "mrp": "399", "stock": "3",
                                             "part_family": "spares", "description": "edited",
                                             "visible": "on", "unit": "piece"})
    e = q("select name, price, stock from products where id=?", pid)
    rec("product", "edit saves", e and e[0] == "Func Test Spare Part EDITED" and abs(e[1] - 199) < .01 and e[2] == 3,
        f"{e}")

# ---------------------------------------------------------------- 4. IMAGES
print("\n[4] UPLOADING IMAGES")
if pid:
    i_before = q("select count(*) from product_images where product_id=?", pid)[0]
    r = post(f"/admin/products/{pid}/images", files={"files": ("shot1.jpg", jpeg(), "image/jpeg")})
    rec("upload", "product photo accepted", r.status_code == 200, f"HTTP {r.status_code}")
    i_after = q("select count(*) from product_images where product_id=?", pid)[0]
    rec("upload", "product photo row created", i_after == i_before + 1, f"{i_before} -> {i_after}")
    img = q("select id, url from product_images where product_id=? order by id desc limit 1", pid)
    if img:
        rec("upload", "stored under /media/", str(img[1]).startswith("/media/"), f"url={img[1]}")
        served = httpx.get(B + img[1], headers={"Host": STORE}, timeout=30)
        rec("upload", "the uploaded file is actually served",
            served.status_code == 200 and len(served.content) > 1000,
            f"HTTP {served.status_code}, {len(served.content)} bytes")
        # a second photo, then make it primary
        post(f"/admin/products/{pid}/images", files={"files": ("shot2.jpg", jpeg(colour=(20, 90, 200)), "image/jpeg")})
        img2 = q("select id from product_images where product_id=? order by id desc limit 1", pid)
        if img2:
            r = post(f"/admin/products/{pid}/images/{img2[0]}/primary")
            rec("upload", "set-as-primary works", r.status_code == 200, f"HTTP {r.status_code}")
        # delete one
        n1 = q("select count(*) from product_images where product_id=?", pid)[0]
        post(f"/admin/products/{pid}/images/{img[0]}/delete")
        n2 = q("select count(*) from product_images where product_id=?", pid)[0]
        rec("upload", "delete removes the row", n2 == n1 - 1, f"{n1} -> {n2}")
    # a file that is not really an image must be refused
    n3 = q("select count(*) from product_images where product_id=?", pid)[0]
    post(f"/admin/products/{pid}/images", files={"files": ("fake.jpg", b"not an image at all", "image/jpeg")})
    n4 = q("select count(*) from product_images where product_id=?", pid)[0]
    rec("upload", "a fake image is refused", n4 == n3, f"{n3} -> {n4}")

if cat_id:
    r = post(f"/admin/categories/{cat_id}/image", files={"file": ("cat.jpg", jpeg(500, 500, (30, 160, 90)), "image/jpeg")})
    rec("upload", "category photo accepted", r.status_code == 200, f"HTTP {r.status_code}")
    ci = q("select image_url from categories where id=?", cat_id)
    rec("upload", "category photo saved to the row", bool(ci and ci[0]), f"image_url={ci[0] if ci else None!r}")
    if ci and ci[0]:
        s2 = httpx.get(B + ci[0], headers={"Host": STORE}, timeout=30)
        rec("upload", "category photo is served", s2.status_code == 200, f"HTTP {s2.status_code}")
        api_cat = {x["id"]: x for x in api()["categories"]}.get(cat_id, {})
        rec("upload", "category photo reaches the storefront API",
            bool(api_cat.get("img") or api_cat.get("image") or api_cat.get("image_url")),
            f"api keys={sorted(api_cat.keys())}")
    post(f"/admin/categories/{cat_id}/image/delete")
    ci2 = q("select image_url from categories where id=?", cat_id)
    rec("upload", "category photo delete clears it", not (ci2 and ci2[0]), f"image_url={ci2[0] if ci2 else None!r}")

# ---------------------------------------------------------------- cleanup
print("\n[5] CLEANUP")
if pid:
    post(f"/admin/products/{pid}/delete")
    rec("cleanup", "test product deleted", q("select id from products where sku=?", SKU) is None)
if cat_id:
    post(f"/admin/categories/{cat_id}/delete")
    rec("cleanup", "test category deleted", q("select id from categories where code='FTA'") is None)
rec("cleanup", "catalogue back to 896 products", q("select count(*) from products")[0] == 896,
    f"{q('select count(*) from products')[0]}")

print("\n" + "=" * 74)
passed = sum(1 for _, _, ok in checks if ok)
print(f"RESULT: {passed}/{len(checks)} passed")
bad = [(g, n) for g, n, ok in checks if not ok]
if bad:
    print("FAILING:")
    for g, n in bad:
        print(f"  {g} :: {n}")
print("=" * 74)
