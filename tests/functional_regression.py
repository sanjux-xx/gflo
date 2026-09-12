import httpx, re, io, json, sqlite3, math
B="http://127.0.0.1:8362"; DB="/agent/workspace/ship/gflo.db"
PW="ShipTestPass1234"; res=[]
def check(n,d,ok,detail=""):
    res.append((n,ok)); print(f"[{'PASS' if ok else 'FAIL'}] {n} {d}"+(f"\n        {detail}" if detail else ""))
def sess(pw=PW,u="gflo"):
    c=httpx.Client(base_url=B, follow_redirects=False)
    t=re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
    c.post("/admin/login", data={"username":u,"password":pw,"csrf_token":t}); return c
def cs(c): return c.cookies.get("gflo_csrf")
def post(c,u,d=None,**kw):
    dd=dict(d or {}); dd["csrf_token"]=cs(c); return c.post(u,data=dd,follow_redirects=True,**kw)
c=sess()
cid=[x["id"] for x in httpx.Client(base_url=B).get("/api/catalog").json()["categories"]][0]
q=lambda s,*a: sqlite3.connect(DB).execute(s,a).fetchone()

print("--- REGRESSION: previously-passing security controls ---")
for p_ in ["' OR '1'='1","%'; DROP TABLE products;--","' UNION SELECT null--"]:
    t=httpx.Client(base_url=B).get("/api/products",params={"q":p_}).json().get("total")
    check("SQLi",f"payload treated literally ({p_[:18]}…)", t==0, f"total={t}")
check("SQLi-intact","products table intact", q("select count(*) from products")[0]>800, f"rows={q('select count(*) from products')[0]}")
for t_ in ["/../backend/app/security.py","/%2e%2e/%2e%2e/etc/passwd","/media/products/../../gflo.db","/....//....//etc/passwd"]:
    r=httpx.Client(base_url=B,follow_redirects=False).get(t_)
    check("traversal",f"blocked {t_[:34]}", r.status_code!=200 or "scrypt" not in r.text, f"status={r.status_code}")
cli=httpx.Client(base_url=B, follow_redirects=False)
for r_ in ["/admin","/admin/products","/admin/settings","/admin/activity","/admin/export.csv","/admin/categories","/admin/brands","/admin/import"]:
    st=cli.get(r_).status_code
    check("authgate",f"unauth {r_} -> {st}", st in (303,401,403))
xss='<img src=x onerror=alert(1)>'
r=c.get("/admin/products", params={"msg":xss}, follow_redirects=True)
check("admin-xss","reflected msg still escaped", xss not in r.text and "&lt;img" in r.text)
r=post(c,"/admin/products/new",{"name":"<script>alert(1)</script>REG","category_id":cid,"price":"10","stock":"5","visible":"on"})
r=c.get("/admin/products", params={"q":"REG"}, follow_redirects=True)
check("admin-xss","stored product name still escaped", "<script>alert(1)</script>REG" not in r.text and "&lt;script&gt;" in r.text)
r=httpx.Client(base_url=B).get("/admin/login?err=<b>x</b>")
check("admin-xss","login err still escaped", "<b>x</b>" not in r.text)
r=cli.post("/admin/import", data={"csrf_token":"x"})
check("no-debug","errors reveal no stack trace", "Traceback" not in r.text)

print("--- REGRESSION: normal admin workflows still work ---")
r=post(c,"/admin/products/new",{"name":"WorkflowProd","category_id":cid,"price":"249.50","mrp":"300","stock":"12","unit":"piece","visible":"on"})
row=q("select id,price,mrp,stock from products where name='WorkflowProd'")
check("crud","create product", row is not None and row[1]==249.5 and row[3]==12, f"row={row}")
pid=row[0]
r=post(c,f"/admin/products/{pid}/edit",{"name":"WorkflowProd v2","category_id":cid,"price":"275","mrp":"320","stock":"7","visible":"on"})
row=q("select name,price,stock from products where id=?",pid)
check("crud","edit product", row[0]=="WorkflowProd v2" and row[1]==275.0 and row[2]==7, f"row={row}")
r=c.post("/admin/products/inline", json={"id":pid,"field":"price","value":"199.99"}, headers={"X-CSRF-Token":cs(c)})
check("crud","inline price edit", r.status_code==200 and r.json().get("price")==199.99, f"{r.status_code} {r.text[:60]}")
r=c.post("/admin/products/inline", json={"id":pid,"field":"stock","value":"33"}, headers={"X-CSRF-Token":cs(c)})
check("crud","inline stock edit", r.status_code==200 and r.json().get("stock")==33)
r=c.post("/admin/products/inline", json={"id":pid,"field":"visible","value":False}, headers={"X-CSRF-Token":cs(c)})
check("crud","inline visibility toggle", r.status_code==200 and r.json().get("visible")==False)
r=post(c,"/admin/products/bulk",{"ids":str(pid),"action":"price_pct","amount":"10","back":"/admin/products"})
pr=q("select price from products where id=?",pid)[0]
check("bulk","price +10% = 219.99", abs(pr-219.99)<0.02, f"price={pr}")
r=post(c,"/admin/products/bulk",{"ids":str(pid),"action":"set_stock","amount":"50","back":"/admin/products"})
check("bulk","set stock", q("select stock from products where id=?",pid)[0]==50)
r=post(c,"/admin/products/bulk",{"ids":str(pid),"action":"show","back":"/admin/products"})
check("bulk","show/hide", q("select visible from products where id=?",pid)[0]==1)
r=post(c,f"/admin/products/{pid}/duplicate")
check("crud","duplicate product", q("select count(*) from products where name like 'WorkflowProd v2 (copy)'")[0]==1)
r=post(c,"/admin/categories/save",{"id":"regcat","name":"Reg Cat","code":"RC","sort_order":"7","hue":"200"})
check("cats","create category", q("select name from categories where id='regcat'")[0]=="Reg Cat")
r=post(c,"/admin/brands/save",{"name":"RegBrand","hue":"120","sort_order":"3"})
check("brands","create brand", q("select count(*) from brands where name='RegBrand'")[0]==1)
r=post(c,"/admin/categories/regcat/delete")
check("cats","delete empty category", q("select count(*) from categories where id='regcat'")[0]==0)
r=post(c,f"/admin/categories/{cid}/delete")
check("cats","in-use category still protected", "still use" in r.text and q("select count(*) from categories where id=?",cid)[0]==1)
# CSV round trip
r=c.get("/admin/export.csv"); csvtext=r.text
check("csv","export returns catalogue", r.status_code==200 and csvtext.count("\n")>800, f"lines={csvtext.count(chr(10))}")
sku=q("select sku from products where id=?",pid)[0]
r=c.post("/admin/import", data={"mode":"update","csrf_token":cs(c)},
         files={"file":("u.csv",f"sku,price,stock\n{sku},888.00,99\n","text/csv")}, follow_redirects=True)
row=q("select price,stock from products where id=?",pid)
check("csv","import updates existing row", row[0]==888.0 and row[1]==99, f"row={row} msg={'1 updated' in r.text}")
r=c.post("/admin/import", data={"mode":"create","csrf_token":cs(c)},
         files={"file":("n.csv",f"sku,name,category_id,price,stock\nREG-NEW-1,CSV Made,{cid},55,4\n","text/csv")}, follow_redirects=True)
check("csv","import creates new row", q("select count(*) from products where sku='REG-NEW-1'")[0]==1)
# image upload + primary + delete
from PIL import Image
buf=io.BytesIO(); Image.new("RGB",(500,500),(10,200,90)).save(buf,format="PNG")
r=c.post(f"/admin/products/{pid}/images", data={"csrf_token":cs(c)}, files={"files":("ok.png",buf.getvalue(),"image/png")}, follow_redirects=True)
imgs=sqlite3.connect(DB).execute("select id,url from product_images where product_id=?",(pid,)).fetchall()
check("images","upload valid PNG", len(imgs)==1, f"imgs={imgs}")
if imgs:
    r=httpx.Client(base_url=B).get(imgs[0][1]); check("images","uploaded photo served", r.status_code==200 and r.content[:4]==b"\x89PNG")
    r=post(c,f"/admin/products/{pid}/images/{imgs[0][0]}/primary"); check("images","set primary", q("select image_url from products where id=?",pid)[0]==imgs[0][1])
    r=post(c,f"/admin/products/{pid}/images/{imgs[0][0]}/delete"); check("images","delete photo", sqlite3.connect(DB).execute("select count(*) from product_images where product_id=?",(pid,)).fetchone()[0]==0)
# settings + password + users
r=post(c,"/admin/settings",{"store_name":"G-FLO","contact_email":"sales@gflo.in","contact_phone":"+91 1","show_prices":"on","por_label":"Price on request"})
check("settings","save settings", "Settings saved" in r.text and q("select value from settings where key='contact_email'")[0]=="sales@gflo.in")
r=post(c,"/admin/settings",{"store_name":"G-FLO","contact_email":"sales@gflo.in"})
check("settings","show_prices switch off", q("select value from settings where key='show_prices'")[0]=="false")
cat=httpx.Client(base_url=B).get("/api/catalog").json()
check("api","showPrices honoured by API", cat["showPrices"]==False and cat["products"][0]["price"] is None)
r=post(c,"/admin/settings",{"store_name":"G-FLO","contact_email":"sales@gflo.in","show_prices":"on"})
r=post(c,"/admin/settings/users",{"username":"regstaff","name":"S","password":"RegStaffPass12","role":"admin"})
check("users","create admin-role user", q("select role from admin_users where username='regstaff'")[0]=="admin")
uid=q("select id from admin_users where username='regstaff'")[0]
c4=sess("RegStaffPass12","regstaff")
r=c4.get("/admin/settings", follow_redirects=False)
check("users","admin-role user CAN reach settings", r.status_code==200, f"status={r.status_code}")
r=post(c,f"/admin/settings/users/{uid}/delete")
check("users","delete user", q("select count(*) from admin_users where username='regstaff'")[0]==0)
owner_id=q("select id from admin_users where is_owner=1")[0]
r=post(c,f"/admin/settings/users/{owner_id}/delete")
still_there=q("select count(*) from admin_users where id=?",owner_id)[0]==1
check("users","owner still protected", still_there and "can&#39;t be removed" in r.text, f"owner_row_present={still_there}")
# deleted user's session must die (H-02 consequence)
r=c4.get("/admin", follow_redirects=False)
check("users","deleted user's session revoked", r.status_code==303, f"status={r.status_code}")
r=post(c,f"/admin/products/{pid}/delete")
check("crud","delete product", q("select count(*) from products where id=?",pid)[0]==0)
# pages render
for pg in ["/admin","/admin/products","/admin/categories","/admin/brands","/admin/import","/admin/settings","/admin/activity","/admin/products/new"]:
    r=c.get(pg, follow_redirects=False); check("pages",f"{pg} renders 200", r.status_code==200, f"status={r.status_code}")
# storefront + api
r=httpx.Client(base_url=B).get("/"); check("store","storefront serves", r.status_code==200 and "G-FLO" in r.text)
r=httpx.Client(base_url=B).get("/api/catalog")
ok=r.status_code==200
try: d=json.loads(r.text); vj=True
except Exception: vj=False
check("api","/api/catalog valid JSON", ok and vj and len(d["products"])>800, f"products={len(d['products']) if vj else 'n/a'}")
r=httpx.Client(base_url=B).get("/api/products", params={"per_page":250})
check("api","per_page cap enforced", r.status_code==422 or len(r.json().get("products",[]))<=200, f"status={r.status_code}")
sku2=q("select sku from products where visible=1 limit 1")[0]
r=httpx.Client(base_url=B).get(f"/api/products/{sku2}")
check("api","product detail", r.status_code==200 and r.json()["sku"]==sku2)
print("="*70)
p=sum(1 for _,o in res if o); print(f"REGRESSION RESULT: {p}/{len(res)} passed")
print("FAILING:", [n for n,o in res if not o] or "none")
