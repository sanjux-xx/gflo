import httpx, base64, json, hmac, hashlib, time, re, io, sqlite3
B="http://127.0.0.1:8361"; DB="/agent/workspace/ship/gflo.db"
SECRET=b"testsecret0123456789"
PW="ShipTestPass1234"
res=[]
def check(fid, desc, passed, detail=""):
    res.append((fid, passed))
    print(f"[{'PASS' if passed else 'FAIL'}] {fid} {desc}" + (f"\n        {detail}" if detail else ""))

def sess(pw=PW, user="gflo"):
    c=httpx.Client(base_url=B, follow_redirects=False)
    t=re.search(r'name="csrf_token" value="([^"]*)"', c.get("/admin/login").text).group(1)
    r=c.post("/admin/login", data={"username":user,"password":pw,"csrf_token":t})
    c._csrf=t
    return c
def csrf(c): return c.cookies.get("gflo_csrf")
def post(c, url, data=None, **kw):
    d=dict(data or {}); d["csrf_token"]=csrf(c)
    return c.post(url, data=d, follow_redirects=True, **kw)
def b64(raw): return base64.urlsafe_b64encode(raw).decode().rstrip("=")
def mint(u, v=0, t=None):
    p=b64(json.dumps({"u":u,"t":int(t or time.time()),"v":v}).encode())
    return p+"."+b64(hmac.new(SECRET,p.encode(),hashlib.sha256).digest())

c=sess()
cid=[x["id"] for x in httpx.Client(base_url=B).get("/api/catalog").json()["categories"]][0]
pid=sqlite3.connect(DB).execute("select id from products limit 1").fetchone()[0]

print("="*74); print("H-01  Infinity/NaN price -> catalog outage"); print("="*74)
r=post(c,"/admin/products/bulk",{"ids":str(pid),"action":"price_pct","amount":"1e308","back":"/admin/products"})
pr=sqlite3.connect(DB).execute("select price from products where id=?",(pid,)).fetchone()[0]
import math
finite = pr is None or math.isfinite(pr)
check("H-01a","bulk 1e308% no longer stores inf", finite, f"stored price={pr}")
r=c.post("/admin/products/inline", json={"id":pid,"field":"price","value":"1e400"}, headers={"X-CSRF-Token":csrf(c)})
check("H-01b","inline rejects non-finite price", r.status_code==400, f"{r.status_code} {r.text[:70]}")
r=httpx.Client(base_url=B).get("/api/catalog")
ok=r.status_code==200
try: json.loads(r.text); valid=True
except Exception: valid=False
check("H-01c","/api/catalog serves valid JSON", ok and valid, f"http={r.status_code} valid_json={valid}")

print("="*74); print("H-02  Session revocation"); print("="*74)
r=httpx.Client(base_url=B,cookies={"gflo_admin":mint("ghost_user",0)}).get("/admin",follow_redirects=False)
check("H-02a","valid-sig session for non-existent user rejected", r.status_code==303, f"status={r.status_code}")
c2=sess(); tok=c2.cookies.get("gflo_admin")
post(c2,"/admin/settings/password",{"current":PW,"new":"NewLongPassword12","confirm":"NewLongPassword12"})
r=httpx.Client(base_url=B,cookies={"gflo_admin":tok}).get("/admin",follow_redirects=False)
check("H-02b","old cookie invalid after password change", r.status_code==303, f"status={r.status_code}")
c3=sess("NewLongPassword12"); tok3=c3.cookies.get("gflo_admin")
post(c3,"/admin/logout")
r=httpx.Client(base_url=B,cookies={"gflo_admin":tok3}).get("/admin",follow_redirects=False)
check("H-02c","token replay after logout rejected", r.status_code==303, f"status={r.status_code}")
c=sess("NewLongPassword12")
post(c,"/admin/settings/password",{"current":"NewLongPassword12","new":PW,"confirm":PW})
c=sess()
r=httpx.Client(base_url=B,cookies={"gflo_admin":mint("gflo",0,1)}).get("/admin",follow_redirects=False)
check("H-02d","expired token still rejected", r.status_code==303, f"status={r.status_code}")

print("="*74); print("M-01  CSRF"); print("="*74)
bare=httpx.Client(base_url=B, cookies={"gflo_admin":c.cookies.get("gflo_admin")})
r=bare.post("/admin/products/bulk", data={"ids":str(pid),"action":"delete","back":"/admin/products"}, follow_redirects=False)
check("M-01a","bulk delete without CSRF token blocked", r.status_code==403, f"status={r.status_code}")
r=bare.post("/admin/settings/users", data={"username":"evil","password":"EvilPassword12"}, follow_redirects=False)
check("M-01b","add-admin without CSRF blocked", r.status_code==403, f"status={r.status_code}")
r=bare.post("/admin/products/inline", json={"id":pid,"field":"price","value":"5"}, follow_redirects=False)
check("M-01c","inline edit without CSRF header blocked", r.status_code==403, f"status={r.status_code}")
r=bare.post("/admin/products/bulk", data={"ids":str(pid),"action":"hide","back":"/admin/products","csrf_token":"wrongvalue"}, follow_redirects=False)
check("M-01d","wrong CSRF token blocked", r.status_code==403, f"status={r.status_code}")
r=post(c,"/admin/products/bulk",{"ids":str(pid),"action":"hide","back":"/admin/products"})
check("M-01e","valid CSRF still allows the action", r.status_code==200 and "hidden" in r.text.lower(), f"status={r.status_code}")
post(c,"/admin/products/bulk",{"ids":str(pid),"action":"show","back":"/admin/products"})

print("="*74); print("M-02  Storefront DOM XSS"); print("="*74)
payload='<img src=x onerror=alert(1)>'
post(c,"/admin/categories/save",{"id":"xsscat","name":payload,"code":"XS","sort_order":"5"})
site=open("/agent/workspace/gflo/gflo-v33/site/gflo.html").read()
raw=len(re.findall(r'\$\{(?:c|b|t)\.name\}', site))
check("M-02","all category/brand name sinks escaped in storefront", raw==0, f"unescaped ${{x.name}} sinks remaining={raw}, U.esc(c.name)={site.count('U.esc(c.name)')}")

print("="*74); print("M-05  Open redirect"); print("="*74)
for tgt,label in [("https://evil.example.com/phish","absolute"),("//evil.example.com","protocol-relative"),("/admin/products","internal")]:
    r=c.get(f"/admin/login?next={tgt}", follow_redirects=False)
    loc=r.headers.get("location","")
    good = (loc=="/admin/products") if label=="internal" else ("evil" not in loc)
    check(f"M-05 ({label})", f"redirect target sanitised -> {loc}", good)

print("="*74); print("M-06/M-07  Upload abuse"); print("="*74)
r=post(c,"/admin/products/new",{"name":"RETESTPROD","category_id":cid,"price":"10","stock":"1","visible":"on"})
npid=sqlite3.connect(DB).execute("select id from products where name='RETESTPROD' order by id desc limit 1").fetchone()[0]
def up(fn, content):
    return c.post(f"/admin/products/{npid}/images", data={"csrf_token":csrf(c)},
                  files={"files":(fn,content,"image/jpeg")}, follow_redirects=True)
r=up("evil.jpg", b"<html><script>alert(1)</script></html>")
n=sqlite3.connect(DB).execute("select count(*) from product_images where product_id=?",(npid,)).fetchone()[0]
check("M-06","non-image bytes with .jpg extension rejected", n==0, f"stored images={n}; msg={'not a readable image' in r.text or 'readable' in r.text}")
from PIL import Image
buf=io.BytesIO(); Image.new("RGB",(9000,9000),(255,0,0)).save(buf,format="PNG")
r=up("bomb.png", buf.getvalue())
n2=sqlite3.connect(DB).execute("select count(*) from product_images where product_id=?",(npid,)).fetchone()[0]
check("M-07","9000x9000 pixel bomb rejected", n2==0, f"stored images={n2}; 'too large' in msg={'too large' in r.text.lower()}")
buf=io.BytesIO(); Image.new("RGB",(600,400),(0,120,255)).save(buf,format="JPEG")
r=up("good.jpg", buf.getvalue())
n3=sqlite3.connect(DB).execute("select count(*) from product_images where product_id=?",(npid,)).fetchone()[0]
check("M-06b","legitimate JPEG still uploads", n3==1, f"stored images={n3}")

print("="*74); print("M-08  CSV import robustness"); print("="*74)
sku=sqlite3.connect(DB).execute("select sku from products where id=?",(pid,)).fetchone()[0]
for field,bad in [("price","not_a_number"),("stock","abc"),("rating","xyz")]:
    r=c.post("/admin/import", data={"mode":"update","csrf_token":csrf(c)},
             files={"file":("x.csv",f"sku,{field}\n{sku},{bad}\n","text/csv")}, follow_redirects=False)
    check(f"M-08 ({field})", "bad cell no longer 500s", r.status_code in (303,200), f"status={r.status_code}")

print("="*74); print("M-09  Security headers"); print("="*74)
h=httpx.Client(base_url=B).get("/admin/login").headers
check("M-09a","X-Frame-Options on admin", h.get("x-frame-options")=="DENY", h.get("x-frame-options",""))
check("M-09b","CSP on admin", "frame-ancestors 'none'" in (h.get("content-security-policy") or ""), (h.get("content-security-policy") or "")[:60])
h2=httpx.Client(base_url=B).get("/").headers
check("M-09c","nosniff on storefront", h2.get("x-content-type-options")=="nosniff", h2.get("x-content-type-options",""))

print("="*74); print("M-10  Privilege separation"); print("="*74)
post(c,"/admin/settings/users",{"username":"staff","name":"Staff","password":"StaffPassword12","role":"editor"})
cs=sess("StaffPassword12","staff")
r=cs.get("/admin/products", follow_redirects=False)
check("M-10pre","editor session authenticates (guards the checks below)", r.status_code==200, f"status={r.status_code}")
r=cs.get("/admin/settings", follow_redirects=False)
check("M-10a","editor blocked from settings page", r.status_code==303, f"status={r.status_code}")
r=post(cs,"/admin/settings/users",{"username":"staff2","password":"StaffPassword34"})
n=sqlite3.connect(DB).execute("select count(*) from admin_users where username='staff2'").fetchone()[0]
check("M-10b","editor cannot create admin accounts", n==0, f"staff2 rows={n}")
r=post(cs,"/admin/settings",{"store_name":"HACKED","contact_email":"a@evil.com"})
sn=sqlite3.connect(DB).execute("select value from settings where key='store_name'").fetchone()[0]
check("M-10c","editor cannot change store settings", sn!="HACKED", f"store_name={sn}")
r=cs.get("/admin/export.csv", follow_redirects=False)
check("M-10d","editor cannot export catalogue", r.status_code==303, f"status={r.status_code}")
r=cs.get("/admin/products", follow_redirects=False)
check("M-10e","editor CAN still edit catalogue", r.status_code==200, f"status={r.status_code}")

print("="*74); print("L-01..L-06"); print("="*74)
post(c,"/admin/products/new",{"name":"=HYPERLINK(0)","category_id":cid,"price":"1","stock":"1","description":"=cmd|'/c calc'!A1","visible":"on"})
r=c.get("/admin/export.csv")
bad=[l for l in r.text.splitlines() if re.search(r'(^|,)=(HYPERLINK|cmd)', l)]
check("L-01","CSV export neutralises formulas", not bad, f"raw formula cells={len(bad)}; guarded={chr(39) in r.text}")
r=c.post("/admin/products/inline", json={"id":pid,"field":"price","value":"-999"}, headers={"X-CSRF-Token":csrf(c)})
check("L-02a","negative price rejected", r.status_code==400, f"{r.status_code} {r.text[:60]}")
r=c.post("/admin/products/inline", json={"id":pid,"field":"stock","value":"-50"}, headers={"X-CSRF-Token":csrf(c)})
check("L-02b","negative stock rejected", r.status_code==400, f"{r.status_code} {r.text[:60]}")
r=httpx.Client(base_url=B).get("/api/docs"); r2=httpx.Client(base_url=B).get("/openapi.json")
check("L-03","API docs + schema disabled", r.status_code==404 and r2.status_code==404, f"docs={r.status_code} openapi={r2.status_code}")
r=httpx.Client(base_url=B).get("/api/health")
check("L-04","health endpoint leaks no counts", "products" not in r.text, r.text[:60])
import statistics
def t_login(u):
    ts=[]
    for _ in range(4):
        cl=httpx.Client(base_url=B, follow_redirects=True)
        tk=re.search(r'name="csrf_token" value="([^"]*)"', cl.get("/admin/login").text).group(1)
        s=time.perf_counter(); cl.post("/admin/login", data={"username":u,"password":"wrongpassword","csrf_token":tk}); ts.append(time.perf_counter()-s)
    return statistics.median(ts)
known, unknown = t_login("gflo"), t_login("nosuchuser_zzz")
ratio = max(known,unknown)/max(min(known,unknown),1e-6)
check("L-05","login timing similar for known vs unknown user", ratio<1.6, f"known={known*1000:.0f}ms unknown={unknown*1000:.0f}ms ratio={ratio:.2f}")
sc=httpx.Client(base_url=B).get("/admin/login").headers.get("set-cookie","")
check("L-06","admin cookies SameSite=strict", "samesite=strict" in sc.lower(), sc[:80])

print("="*74); print("M-03/M-04  Rate limit + lockout"); print("="*74)
blocked=None
for i in range(14):
    r=httpx.Client(base_url=B, follow_redirects=True)
    t=re.search(r'name="csrf_token" value="([^"]*)"', r.get("/admin/login").text).group(1)
    rr=r.post("/admin/login", data={"username":f"spoof{i}","password":"bad","csrf_token":t}, headers={"X-Forwarded-For":f"10.0.0.{i}"})
    if "Too many failed attempts" in rr.text: blocked=i; break
check("M-03","rotating X-Forwarded-For no longer bypasses per-IP limit", blocked is not None, f"locked out after {blocked} spoofed attempts (None = still bypassable)")


print("="*74)
p=sum(1 for _,ok in res if ok); print(f"RESULT: {p}/{len(res)} checks passed")
fails=[f for f,ok in res if not ok]
print("FAILING:", fails if fails else "none")
