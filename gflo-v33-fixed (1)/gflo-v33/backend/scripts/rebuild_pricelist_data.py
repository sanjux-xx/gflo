#!/usr/bin/env python3
"""Build the canonical G-FLO 2026 price-list product set from the PDF-extracted data.

Output: pl_products.json  — a flat list of storefront-ready products.
Every rate here is transcribed from "G-FLO Price List 2026" (Canva PDF, 11 Aug 2026).
Rates printed blank or as "00" in the PDF become price=None -> "Price on request".
"""
import json, os, re

IMG = "/agent/workspace/pl_images"
out = []

def add(code, name, price, cat, group, **kw):
    p = {
        "code": code, "name": name, "price": price, "cat": cat, "group": group,
        "mrp": kw.get("mrp"), "size": kw.get("size", ""), "unit": kw.get("unit", "piece"),
        "pack": kw.get("pack", ""), "colours": kw.get("colours", ""),
        "material": kw.get("material", ""), "note": kw.get("note", ""),
        "image": kw.get("image", ""), "brand": "G-FLO",
    }
    out.append(p)

FAN_COLOURS = "Ivory, Black, White, Silver, Brown, Golden"

# ---------------------------------------------------------------- fan pipes (p4)
ULTRA = [(6,18),(9,27),(10,30),(12,36),(15,45),(18,54),(21,63),(24,72),(30,90),
         (36,108),(42,126),(48,144),(60,180),(72,216),(84,252),(96,288),(120,360)]
for inch, rate in ULTRA:
    add(f"GF-1101-{inch:03d}", f'{inch} Inch Fan Rod — Ultra Heavy (Powder Coated)', rate,
        "fan-pipes", "Ultra Heavy Fan Pipes", size=f"{inch} inch",
        material="180 gm CRC pipe, powder coated", pack="Single box packing",
        colours=FAN_COLOURS, image="fan-rods-ultra.jpg",
        note="Ultra heavy 180 gm CRC down rod, powder coated finish, single box packing.")

HEAVY = [(6,28),(9,28),(10,28),(12,28),(15,36),(18,43),(21,50),(24,57),(30,72),
         (36,86),(42,100),(48,115),(60,144),(72,172),(84,201),(96,230),(120,288)]
for inch, rate in HEAVY:
    add(f"GF-1201-{inch:03d}", f'{inch} Inch Fan Rod — Heavy (180 gm)', rate,
        "fan-pipes", "Heavy Fan Pipes", size=f"{inch} inch",
        material="180 gm pipe", pack="Pouch packing", colours=FAN_COLOURS,
        image="fan-rods-heavy.jpg",
        note="Heavy 180 gm down rod supplied in pouch packing.")

# ------------------------------------------------- grid products (p6-p12) + variants
# rows: code, name, cat, group, price(None=on request), size, note, variants[(label,price)]
GRID = [
 ("GF-2101","Fan Clamp (Extra Heavy)","clamps-hooks","Clamps, Hooks & Anchor Fasteners",20.0,"","Extra-heavy fan hook clamp supplied with studs.",[]),
 ("GF-2102","Fan Clamp (Heavy)","clamps-hooks","Clamps, Hooks & Anchor Fasteners",16.0,"","Heavy-duty fan hook clamp supplied with studs.",[]),
 ("GF-2103","Jhula Hook (S.S.)","clamps-hooks","Clamps, Hooks & Anchor Fasteners",550.0,"","Stainless-steel swing (jhula) hook with fastener studs.",[]),
 ("GF-2104","Jhula Hook (M.S.)","clamps-hooks","Clamps, Hooks & Anchor Fasteners",450.0,"","Mild-steel swing (jhula) hook with fastener studs.",[]),
 ("GF-2105","Anchor Fastner (Double)","clamps-hooks","Clamps, Hooks & Anchor Fasteners",32.0,"","Double anchor fastener with studs.",[]),
 ("GF-2106","Anchor Fastner (Single)","clamps-hooks","Clamps, Hooks & Anchor Fasteners",22.0,"","Single anchor fastener with studs.",[]),
 ("GF-2107","Chokdi Hook With Fastner","clamps-hooks","Clamps, Hooks & Anchor Fasteners",None,"","Chokdi hook supplied with anchor fastener.",[]),
 ("GF-2108","Chokdi Hook","clamps-hooks","Clamps, Hooks & Anchor Fasteners",None,"","Chokdi hook.",[]),

 ("GF-2201","Street Light Stand (PVC)","street-light","Street Light Clamps & Accessories",55.0,"","PVC street-light stand.",[]),
 ("GF-2202","Street Light Wall Clamp","street-light","Street Light Clamps & Accessories",30.0,"","Wall clamp for street-light fittings.",[]),
 ("GF-2203","Street Light Stand","street-light","Street Light Clamps & Accessories",65.0,"","Metal street-light stand.",[]),
 ("GF-2204","Street Light Stand (Powder Coated)","street-light","Street Light Clamps & Accessories",70.0,"","Powder-coated street-light stand.",[]),
 ("GF-2205","Pole Clamp (Double)","street-light","Street Light Clamps & Accessories",140.0,"","Double pole clamp.",[]),
 ("GF-2206","Pole Clamp (Single)","street-light","Street Light Clamps & Accessories",120.0,"","Single pole clamp.",[]),
 ("GF-2207","Solar Street Light Stand","street-light","Street Light Clamps & Accessories",230.0,"","Stand for solar street-light fittings.",[]),
 ("GF-2208","Solar Street Light Stand (PC)","street-light","Street Light Clamps & Accessories",230.0,"","Powder-coated stand for solar street-light fittings.",[]),

 ("GF-3101","Drilling Machine","power-tools","Tools & Accessories",None,"","Corded impact drilling machine.",[]),
 ("GF-3102","Angle Grinder Machine","power-tools","Tools & Accessories",None,"","Corded angle grinder.",[]),
 ("GF-3103","Screw Machine","power-tools","Tools & Accessories",None,"","Screwdriver / driver machine.",[]),

 ("GF-3201","Immersion Rod — Waterproof Triangle","electrical","Tools & Accessories",210.0,"Triangle","Waterproof triangle-element immersion water heater.",[]),
 ("GF-3202","Immersion Rod — Waterproof Ghadi","electrical","Tools & Accessories",210.0,"Ghadi","Waterproof ghadi-element immersion water heater.",[]),
 ("GF-3203","Immersion Rod — Waterproof Normal","electrical","Tools & Accessories",190.0,"Normal","Waterproof immersion water heater, normal element.",[]),

 ("GF-4101","Machine Screw","fasteners","Tools & Accessories",145.0,"3/16 inch x 1.0 / 1.5 / 2.0 / 2.5 / 3.0 / 4.0 inch",
  "Machine screws sold by weight. Sizes: 3/16 inch in 1.0, 1.5, 2.0, 2.5, 3.0 and 4.0 inch lengths.",[]),
 ("GF-4102","Stone Cutting Blade (Heavy)","blades","Tools & Accessories",None,"","Heavy-duty diamond stone-cutting blade.",[]),
 ("GF-4103","Stone Cutting Blade","blades","Tools & Accessories",None,"","Diamond stone-cutting blade.",[]),
 ("GF-4104","Steel Cutting Blade","blades","Tools & Accessories",None,"","Abrasive steel-cutting wheel.",[]),
 ("GF-4105","Wood Cutting Blade","blades","Tools & Accessories",None,"","TCT wood-cutting saw blade.",[]),
 ("GF-4106","Tester","power-tools","Tools & Accessories",None,"","Neon line tester screwdriver.",
  [("Small",15.0),("Big",25.0)]),
 ("GF-4107","Reversible Screw Driver","power-tools","Tools & Accessories",None,"","Reversible-bit screwdriver.",
  [("4 inch",22.0),("6 inch",24.0),("8 inch",26.0),("10 inch",28.0)]),
 ("GF-4108","Plier","power-tools","Tools & Accessories",None,"","Insulated combination plier.",
  [("Sparton",130.0),("Marc",110.0),("Normal",80.0)]),
 ("GF-4109","Wire Stripper","power-tools","Tools & Accessories",None,"","Wire stripping / cutting plier.",
  [("Auto",260.0),("Normal",30.0)]),
 ("GF-4110","Measuring Auto Tape","measuring","Other Accessories",40.0,"3 m","Auto-lock steel measuring tape.",[]),
 ("GF-4111","Measuring Tape","measuring","Other Accessories",None,"","Steel measuring tape.",
  [("3 m",40.0),("5 m",55.0)]),

 ("GF-4201","Fan Capacitor","electrical","Electrical Accessories",None,"","Ceiling-fan running capacitor.",[]),
 ("GF-4202","Bearing Set (6201 & 6202)","electrical","Electrical Accessories",40.0,"6201 + 6202","Sealed bearing set — 6201 and 6202.",[]),
 ("GF-4203","Fan Stator","electrical","Electrical Accessories",None,"","Copper-wound ceiling-fan stator.",
  [("24 slot",230.0),("48/12",230.0)]),
 ("GF-4204","Curtain Spring","electrical","Electrical Accessories",None,"","Steel curtain spring wire.",
  [("15 m",180.0),("30 m",320.0)]),
 ("GF-4205","Electrical Tape","electrical","Electrical Accessories",10.0,"","PVC electrical insulation tape.",[]),
 ("GF-4206","Push Connector","electrical","Electrical Accessories",1.5,"","Push-in wire connector.",[]),
 ("GF-4207","PVC Child Safety Plug","electrical","Electrical Accessories",2.0,"","PVC child-safety socket plug.",[]),
 ("GF-4208","Nylon Closer","electrical","Electrical Accessories",0.35,"","Nylon wire closer / end cap.",[]),
 ("GF-4209","Meter Box","electrical","Other Accessories",210.0,"","Single-phase energy meter box.",[]),
 ("GF-4210","HDMI Cable","electrical","Other Accessories",None,"","HDMI cable.",
  [("3 m",110.0),("5 m",150.0)]),

 ("GF-4301","S.S. Wire Connection Pipe","plumbing","Plumbing Accessories",None,"","Stainless-steel braided connection pipe.",
  [("18 inch",80.0),("24 inch",110.0)]),
 ("GF-4302","PTMT Connection Pipe","plumbing","Plumbing Accessories",None,"","PTMT flexible connection pipe.",
  [("18 inch",60.0),("24 inch",80.0)]),
 ("GF-4303","Washing Machine Inlet Pipe","plumbing","Plumbing Accessories",None,"","Washing-machine inlet hose.",
  [("1.5 m",105.0),("2 m",130.0),("3 m",160.0)]),
 ("GF-4304","Washing Machine Outlet Pipe","plumbing","Plumbing Accessories",None,"","Washing-machine outlet hose.",
  [("1.5 m",50.0),("3 m",80.0)]),
 ("GF-4305","Tap Adaptor","plumbing","Plumbing Accessories",35.0,"","Tap adaptor.",[]),
 ("GF-4306","Hose Clamp","plumbing","Plumbing Accessories",None,"","Steel worm-drive hose clamp.",
  [("3/4 inch",2.5),("1 inch",3.0),("1.25 inch",5.0),("1.5 inch",5.5)]),
 ("GF-4307","Waterproof Tape","plumbing","Plumbing Accessories",None,"","High-polymer butyl rubber waterproof tape.",[]),
 ("GF-4308","Teflon Tape","plumbing","Plumbing Accessories",None,"","PTFE thread-seal (teflon) tape.",
  [("10 m",6.5),("12 m",8.0)]),
]

GRID_PACK = {"GF-4205":"30 pcs","GF-4206":"1000 pcs","GF-4207":"1000 pcs",
             "GF-4208":"1000 pcs","GF-4305":"50 pcs"}

for code, name, cat, group, price, size, note, variants in GRID:
    img = code + ".jpg" if os.path.exists(os.path.join(IMG, code + ".jpg")) else ""
    pack = GRID_PACK.get(code, "")
    unit = "kg" if code == "GF-4101" else "piece"
    if not variants:
        add(code, name, price, cat, group, size=size, note=note, image=img, pack=pack, unit=unit)
    else:
        for label, vprice in variants:
            slug = re.sub(r"[^A-Z0-9]+", "", label.upper())
            add(f"{code}-{slug}", f"{name} ({label})", vprice, cat, group,
                size=label, note=note, image=img, pack=pack, unit=unit)

# ----------------------------------------------------------- eye-hook / bolt (p13)
for size, rate in [("6 mm",10.0),("8 mm",15.0),("10 mm",22.0),("12 mm",35.0)]:
    n = size.split()[0]
    add(f"GF-2110-{n}MM", f"Eye-Hook {size}", rate, "clamps-hooks",
        "Clamps, Hooks & Anchor Fasteners", size=size, image="tbl-p13-0.jpg",
        note="Eye-hook with anchor fastener stud.")
for size, rate in [("6 mm",8.0),("8 mm",10.0),("10 mm",14.0),("12 mm",20.0)]:
    n = size.split()[0]
    add(f"GF-2120-{n}MM", f"Bolt Fastner {size}", rate, "fasteners",
        "Clamps, Hooks & Anchor Fasteners", size=size, image="tbl-p13-1.jpg",
        note="Bolt-type anchor fastener.")

# ------------------------------------------------------------ nylon cable tie (p14)
TIES = [("100 X 1.8",600),("100 X 2.5",600),("150 X 2.5",300),("150 X 3.0",400),
        ("150 X 3.6",200),("200 X 2.5",240),("200 X 3.0",200),("200 X 3.6",180),
        ("200 X 4.8",180),("250 X 3.0",140),("250 X 3.6",120),("250 X 4.8",100),
        ("300 X 3.2",140),("300 X 3.6",100),("300 X 4.8",100),("350 X 3.6",100),
        ("350 X 4.8",120),("400 X 3.6",130),("400 X 4.8",80),("450 X 4.8",60)]
for size, pkt_per_bag in TIES:
    slug = size.replace(" X ", "X").replace(" ", "")
    add(f"GF-2130-{slug}", f"Nylon Cable Tie {size} mm", None, "cable-management",
        "Cable Management", size=size + " mm", unit="packet",
        pack=f"100 pcs per packet · {pkt_per_bag} packets per bag",
        note="Nylon 66 self-locking cable tie. Sold by packet.")

# ------------------------------------------------------------ cable nail clip (p15)
CLIPS = [("4 mm",750),("5 mm",550),("6 mm",450),("7 mm",400),("8 mm",300),("9 mm",220),
         ("10 mm",200),("12 mm",140),("14 mm",100),("16 mm",80),("18 mm",60),
         ("20 mm",50),("25 mm",35),("Oval Batten",700),("Flat Batten",700)]
for size, pkt_per_bag in CLIPS:
    slug = re.sub(r"[^A-Z0-9]+", "", size.upper())
    label = size if "Batten" in size else f"Cable Nail Clip {size}"
    if "Batten" in size:
        label = f"{size} Clip"
    add(f"GF-2140-{slug}", label, None, "cable-management", "Cable Management",
        size=size, unit="packet", image="tbl-p15-0.jpg",
        pack=f"100 pcs per packet · {pkt_per_bag} packets per bag",
        note="Cable nail clip with masonry pin. Sold by packet.")

# ---------------------------------------------------------------- screws (p16)
DRYWALL = [("13 X 6",170,1500),("16 X 6",175,1000),("19 X 6",190,1000),("25 X 6",230,1000),
           ("32 X 6",290,500),("38 X 6",340,500),("50 X 6",490,500),("60 X 6",650,400),
           ("75 X 6",790,300)]
for size, rate, per_box in DRYWALL:
    slug = size.replace(" X ", "X").replace(" ", "")
    add(f"GF-2150-{slug}", f"Drywall Screw {size} (Black)", float(rate), "fasteners",
        "Screws", size=size + " mm", unit="1000 pcs", image="tbl-p16-0.jpg",
        pack=f"{per_box} pcs per box", note="Black phosphated drywall screw. Rate is per 1000 pcs.")
SELFDRILL = [("13 X 7",248),("16 X 7",287),("19 X 7",319),("25 X 7",398),
             ("32 X 7",540),("38 X 7",635),("50 X 7",809)]
for size, rate in SELFDRILL:
    slug = size.replace(" X ", "X").replace(" ", "")
    add(f"GF-2160-{slug}", f"Self Drilling Screw {size} (Chrome)", float(rate), "fasteners",
        "Screws", size=size + " mm", unit="1000 pcs", image="tbl-p16-2.jpg",
        note="Chrome self-drilling screw. Rate is per 1000 pcs.")

# ------------------------------------------------------- flexible pipe / plug (p17)
# NOTE: the PDF reuses codes GF-3101.. and GF-3201.. here (already used by tools /
# immersion rods on p8). Suffixed -FP / -WP to keep SKUs unique; flagged for review.
PIPES = [("GF-3101","10 mm",100),("GF-3102","16 mm",50),("GF-3103","20 mm",50),
         ("GF-3104","25 mm",25),("GF-3105","32 mm",25),("GF-3106","40 mm",20),
         ("GF-3107","50 mm",15)]
for code, size, mtr in PIPES:
    for colour, img in [("White & Grey","tbl-p17-0.jpg"),("Black","tbl-p17-2.jpg")]:
        cslug = "WG" if colour.startswith("White") else "BK"
        add(f"{code}-FP-{cslug}", f"Flexible Pipe {size} — {colour}", None, "plumbing",
            "Flexible Pipe", size=f"{size} ({mtr} Mtr. coil)", unit="coil",
            colours=colour, image=img, pack=f"{mtr} metre coil",
            note=f"PVC flexible conduit pipe, {size}, {mtr} metre coil. Duplicate code {code} in the printed list.")
for code, size in [("GF-3201","25 mm"),("GF-3202","35 mm"),("GF-3203","38 mm"),("GF-3204","50 mm")]:
    add(f"{code}-WP", f"PVC Wall Plug {size}", None, "fasteners", "PVC Wall Plug",
        size=size, image="tbl-p17-3.jpg",
        note=f"PVC wall plug / gitti. Duplicate code {code} in the printed list.")

# ------------------------------------------------------------------ menscore (p12)
MENS = [("16/20",2,"2.5 Yard","-","Wire"),("14/42",2,"2.5 Yard","-","Wire"),
        ("24/20",2,"2.5 Yard","-","Wire"),("23/60",2,"2 Yard","2","Flad"),
        ("23/60",2,"2 Yard","2","With garment"),("14/60",2,"2 Yard","2",""),
        ("14/60",2,"3 Yard","2",""),("23/60",3,"2 Yard","3",""),
        ("23/60",3,"3 Yard","3",""),("23/48",3,"3 Yard","3",""),
        ("23/42",3,"3 Yard","3",""),("23/60",3,"3 Yard","3","Cotton"),
        ("40/40",3,"3 Yard","3","Cotton"),("23/52",3,"3 Yard","3","")]
seen = {}
for prod, pin, length, core, note in MENS:
    key = re.sub(r"[^0-9]", "", prod) + f"P{pin}" + re.sub(r"[^0-9]", "", length) + re.sub(r"[^A-Z]", "", note.upper())
    seen[key] = seen.get(key, 0) + 1
    suffix = "" if seen[key] == 1 else f"-{seen[key]}"
    bits = [f"{prod}", f"{pin} pin", length]
    if core != "-":
        bits.append(f"{core} core")
    label = "Menscore Wire " + " · ".join(bits) + (f" ({note})" if note else "")
    add(f"GF-4211-{key}{suffix}", label, None, "cable-management", "Menscore",
        size=f"{prod} · {pin} pin · {length}" + (f" · {core} core" if core != "-" else ""),
        note=("Menscore power cord / wire. " + (note + ". " if note else "")).strip())

json.dump(out, open("/agent/workspace/pl_products.json", "w"), indent=1)
priced = sum(1 for p in out if p["price"] is not None)
withimg = sum(1 for p in out if p["image"])
print(f"products: {len(out)}  priced: {priced}  on-request: {len(out)-priced}  with photo: {withimg}")
from collections import Counter
for c, n in Counter(p["cat"] for p in out).most_common():
    print(f"  {c:18s} {n}")
codes = [p["code"] for p in out]
dupes = [c for c, n in Counter(codes).items() if n > 1]
print("duplicate SKUs:", dupes or "none")
