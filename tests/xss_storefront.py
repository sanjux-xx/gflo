"""Stored-XSS check in a real browser: puts script into category/product names,
product details and shop settings through the admin, then opens every shop and
admin page and checks nothing ran.  Server on port 9870, scratch data dir.
  python tests/xss_storefront.py [all|set|cat|prod]
"""
import re, sys, httpx
from playwright.sync_api import sync_playwright
B="http://127.0.0.1:9870"; ok=[]
def rec(n,c,d=""): ok.append(bool(c)); print(f"  [{'PASS' if c else 'FAIL'}] {n}"+(f"  {d}" if d and not c else ""))
c=httpx.Client(base_url=B,follow_redirects=False,timeout=60)
t=re.search(r'name="csrf_token" value="([^"]*)"',c.get("/admin/login").text).group(1)
c.post("/admin/login",data={"username":"gflo","password":"PosterTestPass123","csrf_token":t})
def post(u,d): d=dict(d); d["csrf_token"]=c.cookies.get("gflo_csrf"); return c.post(u,data=d,follow_redirects=True)
X='<img src=x onerror="window.__pwned=1">'
MODE=__import__("sys").argv[1] if len(__import__("sys").argv)>1 else "all"
if MODE in ("all","cat"): post("/admin/categories/save",{"name":"Cat"+X,"id":"xss-cat","description":"Desc"+X})
if MODE in ("all","prod","cat"): post("/admin/categories/save",{"name":"Plain Cat","id":"xss-cat"}) if MODE=="prod" else None
if MODE in ("all","prod"): post("/admin/products/new",{"sku":"XSS-1","name":"Prod"+X,"category_id":"xss-cat","price":"100","stock":"5","unit":"piece","visible":"on",
     "description":"D"+X,"brand_names":"B"+X,"material":"M"+X,"warranty":"W"+X})
if MODE in ("cat",): post("/admin/products/new",{"sku":"XSS-1","name":"Plain","category_id":"xss-cat","price":"100","stock":"5","unit":"piece","visible":"on"})
if MODE in ("all","set"): r=post("/admin/settings",{"store_name":"G-FLO","contact_email":"a@b.c"+X,"contact_phone":"+91 1"+X,"contact_phone2":"","contact_address":"Addr"+X,
       "whatsapp":"+91 2"+X,"show_prices":"on","price_note":"N"+X,"por_label":"P"+X})
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1280,"height":900}); dialogs=[]
    pg.on("dialog", lambda d:(dialogs.append(d.message), d.dismiss()))
    for path in ["/", "/categories", "/c/xss-cat", "/p/XSS-1", "/search/Prod", "/support", "/contact", "/deals", "/track"]:
        pg.goto(B+path); pg.wait_for_timeout(1500)
        rec(f"no script ran on {path}", not pg.evaluate("!!window.__pwned"))
    pg.evaluate("localStorage.setItem('gflo:cart', JSON.stringify([{sku:'XSS-1',qty:1}]))")
    for path in ["/cart","/checkout"]:
        pg.goto(B+path); pg.wait_for_timeout(1500); rec(f"no script ran on {path}", not pg.evaluate("!!window.__pwned"))
    pg.fill("#f-name","X Test"); pg.fill("#f-phone","9123455555"); pg.fill("#f-addr","Flat 1, Road"); pg.fill("#f-pin","411001"); pg.fill("#f-city","Pune")
    pg.click("#co-form [type=submit]"); pg.wait_for_timeout(2500)
    rec("no script ran on order page", not pg.evaluate("!!window.__pwned"))
    pg.locator("[data-invoice]").first.click(); pg.wait_for_timeout(600)
    rec("no script ran in order summary popup", not pg.evaluate("!!window.__pwned"))
    pg.goto(B+"/track"); pg.fill("#trk-phone","9123455555"); pg.click("#trk-go"); pg.wait_for_timeout(1500)
    rec("no script ran on tracking results", not pg.evaluate("!!window.__pwned"))
    pg.goto(B+"/account/orders"); pg.wait_for_timeout(1500); rec("no script ran on account orders", not pg.evaluate("!!window.__pwned"))
    a=b.new_page(); a.context.add_cookies([{"name":k,"value":v,"url":B} for k,v in c.cookies.items()])
    for path in ["/admin","/admin/products","/admin/categories","/admin/orders","/admin/settings","/admin/activity"]:
        a.goto(B+path); a.wait_for_timeout(800); rec(f"no script ran in admin {path}", not a.evaluate("!!window.__pwned"))
    rec("no pop-ups", not dialogs, dialogs)
print(f"{sum(ok)}/{len(ok)} passed"); sys.exit(0 if all(ok) else 1)
