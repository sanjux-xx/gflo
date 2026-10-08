"""Category / product photos must survive pressing Save.

Regression for: "category images load, then vanish after saving". The category
form used to overwrite the photo with its empty "Tile image" text box.

Run against a SCRATCH database (creates/edits categories and uploads files):
  DATA_DIR=/tmp/cdata SITE_DIR=/path/to/site SECRET_KEY=... \
  uvicorn app.main:app --host 127.0.0.1 --port 9700 --no-proxy-headers
  python tests/category_photo_flows.py
"""
import io, re, sys
import httpx
from PIL import Image

B = "http://127.0.0.1:9700"
USER, PW = "gflo", "PosterTestPass123"
checks = []


def rec(name, ok, detail=""):
    checks.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail and not ok else ""))


def jpg(colour=(30, 120, 200)):
    b = io.BytesIO(); Image.new("RGB", (600, 600), colour).save(b, "JPEG"); return b.getvalue()


c = httpx.Client(base_url=B, follow_redirects=False, timeout=30)
tok = re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
assert c.post("/admin/login", data={"username": USER, "password": PW, "csrf_token": tok}).status_code == 303


def post(url, data=None, files=None):
    d = dict(data or {}); d["csrf_token"] = c.cookies.get("gflo_csrf")
    return c.post(url, data=d, files=files, follow_redirects=True)


def cat(cid):
    for x in httpx.get(B + "/api/catalog", timeout=60).json()["categories"]:
        if x["id"] == cid:
            return x
    return None


def served(url):
    return bool(url) and httpx.get(B + url).status_code == 200


print("=" * 70)
print("category photo: upload, then press Save (the reported bug)")
post("/admin/categories/save", {"name": "Photo Test Cat", "id": "photo-test-cat", "sort_order": "500"})
post("/admin/categories/photo-test-cat/image", files={"file": ("a.jpg", jpg(), "image/jpeg")})
img1 = (cat("photo-test-cat") or {}).get("img", "")
rec("row upload sets the photo", img1.startswith("/media/") and served(img1), img1)
# Save the form exactly as a user would after uploading: tile-image box empty
post("/admin/categories/save", {"name": "Photo Test Cat (renamed)", "id": "photo-test-cat",
                                "description": "edited", "image_url": "", "sort_order": "500"})
after = cat("photo-test-cat") or {}
rec("pressing Save keeps the photo", after.get("img") == img1, f"before={img1} after={after.get('img')}")
rec("...and the other edits are saved", after.get("name") == "Photo Test Cat (renamed)")
rec("...and the photo file still loads", served(after.get("img")))

print("category photo: choose a file inside the Add-category form")
post("/admin/categories/save", {"name": "Form Upload Cat", "id": "form-upload-cat"},
     files={"image_file": ("b.jpg", jpg((200, 40, 40)), "image/jpeg")})
img2 = (cat("form-upload-cat") or {}).get("img", "")
rec("new category created with its photo in one save", img2.startswith("/media/") and served(img2), img2)
post("/admin/categories/save", {"name": "Form Upload Cat", "id": "form-upload-cat"},
     files={"image_file": ("c.jpg", jpg((40, 200, 40)), "image/jpeg")})
img3 = (cat("form-upload-cat") or {}).get("img", "")
rec("choosing a new file replaces the photo", img3 and img3 != img2 and served(img3))
rec("...and the old file is cleaned up", httpx.get(B + img2).status_code == 404)

print("category photo: paste link / remove / bad input")
post("/admin/categories/save", {"name": "Form Upload Cat", "id": "form-upload-cat",
                                "image_url": "/product-photos/geyser/geyser-element-kettle.jpg"})
rec("pasting a link sets it", (cat("form-upload-cat") or {}).get("img") == "/product-photos/geyser/geyser-element-kettle.jpg")
r = post("/admin/categories/save", {"name": "Form Upload Cat", "id": "form-upload-cat",
                                    "image_url": '/x" onerror="alert(1)'})
rec("a link that could inject script is refused", "isn&#39;t valid" in r.text or "isn't valid" in r.text)
rec("...and the previous photo is untouched",
    (cat("form-upload-cat") or {}).get("img") == "/product-photos/geyser/geyser-element-kettle.jpg")
r = post("/admin/categories/save", {"name": "Form Upload Cat", "id": "form-upload-cat"},
         files={"image_file": ("evil.jpg", b"<script>alert(1)</script>", "image/jpeg")})
rec("a non-image file is refused", "readable image" in r.text)
post("/admin/categories/photo-test-cat/image/delete")
rec("Remove photo button still clears it", (cat("photo-test-cat") or {}).get("img") == "")

print("product photo: upload, then press Save with the link box empty")
r = post("/admin/products/new", {"name": "Photo Keep Test", "category_id": "geyser", "price": "10",
                                 "stock": "1", "visible": "on"})
m = re.search(r"/admin/products/(\d+)/edit", str(r.url))
pid = m.group(1) if m else None
rec("test product created", pid is not None, str(r.url))
if pid:
    post(f"/admin/products/{pid}/images", files={"files": ("p.jpg", jpg((90, 90, 90)), "image/jpeg")})
    page = c.get(f"/admin/products/{pid}/edit").text
    main = re.search(r'id="image_url" name="image_url" value="([^"]*)"', page)
    main = main.group(1) if main else ""
    rec("upload sets the main photo", main.startswith("/media/"), main)
    post(f"/admin/products/{pid}/edit", {"name": "Photo Keep Test", "category_id": "geyser",
                                          "price": "12", "stock": "1", "visible": "on", "image_url": ""})
    page = c.get(f"/admin/products/{pid}/edit").text
    kept = re.search(r'id="image_url" name="image_url" value="([^"]*)"', page)
    rec("saving with an empty link box keeps the photo", kept and kept.group(1) == main,
        kept.group(1) if kept else "none")
    post(f"/admin/products/{pid}/delete")

# tidy up the test categories
for cid in ("photo-test-cat", "form-upload-cat"):
    post(f"/admin/categories/{cid}/delete")

print("=" * 70)
print(f"{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
