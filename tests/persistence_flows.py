"""Does admin work survive a redeploy?  (Postgres + photos-in-database setup)

  python tests/persistence_flows.py setup  B=... CSV=path/to/geyser-heating-elements.csv
  -> stop the server, DELETE its DATA_DIR (that's what a redeploy does), start it again
  python tests/persistence_flows.py verify B=...

setup writes /tmp/persist_state.json with what it created; verify checks every item
is still there and every photo still loads byte-for-byte.
"""
import hashlib, io, json, re, sys
import httpx
from PIL import Image

args = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
B = args.get("B", "http://127.0.0.1:9901")
USER, PW = "gflo", "PosterTestPass123"
STATE = "/tmp/persist_state.json"
checks = []


def rec(name, ok, detail=""):
    checks.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail and not ok else ""))


def jpg(colour):
    b = io.BytesIO(); Image.new("RGB", (640, 640), colour).save(b, "JPEG"); return b.getvalue()


def sha(url):
    r = httpx.get(B + url, timeout=30)
    return hashlib.sha256(r.content).hexdigest() if r.status_code == 200 else None


def login():
    c = httpx.Client(base_url=B, follow_redirects=False, timeout=60)
    tok = re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
    assert c.post("/admin/login", data={"username": USER, "password": PW, "csrf_token": tok}).status_code == 303
    return c


def catalog():
    return httpx.get(B + "/api/catalog", timeout=60).json()


if sys.argv[1] == "setup":
    c = login()

    def post(url, data=None, files=None):
        d = dict(data or {}); d["csrf_token"] = c.cookies.get("gflo_csrf")
        return c.post(url, data=d, files=files, follow_redirects=True)

    st = {}
    # 1) photo on an existing category (row upload button)
    post("/admin/categories/geyser/image", files={"file": ("g.jpg", jpg((200, 60, 30)), "image/jpeg")})
    # 2) brand-new category created with its photo
    post("/admin/categories/save", {"name": "Persist Test Category", "id": "persist-test"},
         files={"image_file": ("p.jpg", jpg((30, 160, 90)), "image/jpeg")})
    cats = {x["id"]: x for x in catalog()["categories"]}
    st["cat_geyser_img"] = cats["geyser"]["img"]
    st["cat_new_img"] = cats["persist-test"]["img"]
    # 3) poster
    post("/admin/posters/new", {"title": "Persist Poster", "style": "popup", "frequency": "daily", "active": "on"},
         files={"image": ("n.jpg", jpg((120, 20, 160)), "image/jpeg")})
    st["poster_img"] = httpx.get(B + "/api/posters").json()["popup"]["img"]
    # 4) the geyser CSV import
    r = post("/admin/import", {"mode": "create"},
             files={"file": ("g.csv", open(args["CSV"], "rb"), "text/csv")})
    m = re.search(r"Import done[^<]*", r.text); st["import_msg"] = m.group(0) if m else ""
    # 5) a product photo upload + a price change
    pid = re.search(r"/admin/products/(\d+)/edit", c.get("/admin/products?q=GF-GY-ELE-0901").text).group(1)
    post(f"/admin/products/{pid}/images", files={"files": ("x.jpg", jpg((10, 10, 200)), "image/jpeg")})
    st["pid"] = pid
    prod = [p for p in catalog()["products"] if p["sku"] == "GF-GY-ELE-0901"][0]
    st["prod_imgs"] = prod["imgs"]
    st["hashes"] = {u: sha(u) for u in [st["cat_geyser_img"], st["cat_new_img"], st["poster_img"], *st["prod_imgs"]]}
    st["counts"] = {"products": len(catalog()["products"]), "categories": len(catalog()["categories"])}
    json.dump(st, open(STATE, "w"), indent=1)
    print("setup done:", json.dumps(st["counts"]), st["import_msg"])
    rec("import created the 26 geyser products", "26 created" in st["import_msg"], st["import_msg"])
    rec("all new photos load before the redeploy", all(st["hashes"].values()))

else:
    st = json.load(open(STATE))
    cat = catalog()
    cats = {x["id"]: x for x in cat["categories"]}
    rec("same number of products after redeploy",
        len(cat["products"]) == st["counts"]["products"], f'{len(cat["products"])} vs {st["counts"]["products"]}')
    rec("same number of categories after redeploy", len(cat["categories"]) == st["counts"]["categories"])
    rec("the 26 imported geyser products are still there",
        sum(1 for p in cat["products"] if p["sku"].startswith("GF-GY-ELE")) == 26)
    rec("new category still exists", "persist-test" in cats)
    rec("geyser category keeps its uploaded photo", cats.get("geyser", {}).get("img") == st["cat_geyser_img"])
    rec("new category keeps its photo", cats.get("persist-test", {}).get("img") == st["cat_new_img"])
    pop = httpx.get(B + "/api/posters").json().get("popup") or {}
    rec("poster is still live", pop.get("img") == st["poster_img"])
    for u, h in st["hashes"].items():
        rec(f"photo still loads, identical: {u}", sha(u) == h)
    # photos that came with the code (seeded products) must load even with an empty disk
    seeded = [p for p in cat["products"] if p["img"].startswith("/media/")]
    sample = seeded[:: max(1, len(seeded) // 25)]
    ok = [httpx.get(B + p["img"], timeout=30).status_code == 200 for p in sample]
    rec(f"built-in product photos load ({sum(ok)}/{len(ok)} sampled incl. price-list, tools, filter)", all(ok))
    filt = [p for p in seeded if "/filter/" in p["img"]][:3]
    rec("Filter category photos load", filt and all(httpx.get(B + p["img"]).status_code == 200 for p in filt))
    # geyser photos from the repo folder
    g = [p for p in cat["products"] if p["sku"].startswith("GF-GY-ELE")][:3]
    rec("geyser element photos (repo folder) load", all(httpx.get(B + p["img"]).status_code == 200 for p in g))
    rec("can still sign in to admin", login() is not None)

print("=" * 60)
print(f"{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
