#!/usr/bin/env python3
"""
EU Market Entry Radar - data fetch + page build.

Pulls four public, no-auth feeds and writes data.json, then injects that JSON
into index.template.html to produce index.html (single self-contained file).

Feeds
  1. Eurostat isoc_ec_ib20  - % of individuals (16-74) who bought online in the last 12 months
  2. Eurostat isoc_ec_ibos  - % of individuals who bought from sellers in other countries (last 3 months, 2023 = final year)
  3. World Bank SP.POP.1564.TO, NY.GDP.PCAP.CD - working-age population and GDP per capita
  4. Frankfurter (ECB reference rates) - GBP vs each non-euro currency, last 12 months
  5. ibericode/vat-rates - standard VAT rate by country (community-maintained, dated entries)

Run:  python3 build.py
"""
import json, math, sys, datetime as dt, urllib.request, urllib.parse, statistics

TODAY = dt.date.today()
ONE_YEAR_AGO = TODAY - dt.timedelta(days=365)

# EU27 + Norway, Switzerland; UK shown as home reference
COUNTRIES = {
 "AT":("Austria","AUT","EUR"),"BE":("Belgium","BEL","EUR"),"BG":("Bulgaria","BGR","EUR"),
 "HR":("Croatia","HRV","EUR"),"CY":("Cyprus","CYP","EUR"),"CZ":("Czechia","CZE","CZK"),
 "DK":("Denmark","DNK","DKK"),"EE":("Estonia","EST","EUR"),"FI":("Finland","FIN","EUR"),
 "FR":("France","FRA","EUR"),"DE":("Germany","DEU","EUR"),"EL":("Greece","GRC","EUR"),
 "HU":("Hungary","HUN","HUF"),"IE":("Ireland","IRL","EUR"),"IT":("Italy","ITA","EUR"),
 "LV":("Latvia","LVA","EUR"),"LT":("Lithuania","LTU","EUR"),"LU":("Luxembourg","LUX","EUR"),
 "MT":("Malta","MLT","EUR"),"NL":("Netherlands","NLD","EUR"),"PL":("Poland","POL","PLN"),
 "PT":("Portugal","PRT","EUR"),"RO":("Romania","ROU","RON"),"SK":("Slovakia","SVK","EUR"),
 "SI":("Slovenia","SVN","EUR"),"ES":("Spain","ESP","EUR"),"SE":("Sweden","SWE","SEK"),
 "NO":("Norway","NOR","NOK"),"CH":("Switzerland","CHE","CHF"),
 "UK":("United Kingdom","GBR","GBP"),
}
EU27 = {c for c in COUNTRIES if c not in ("NO","CH","UK")}

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent":"market-radar/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def eurostat(dataset, params):
    base = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
    q = "&".join(f"{k}={v}" for k,v in params) + "&format=JSON&lang=EN"
    d = get_json(f"{base}?{q}")
    dims = d["id"]; size = d["size"]
    idx = {k: d["dimension"][k]["category"]["index"] for k in dims}
    inv = {k: {v:kk for kk,v in idx[k].items()} for k in dims}
    out = {}
    for flat, val in d["value"].items():
        flat = int(flat); coords = []
        for s in reversed(size):
            coords.append(flat % s); flat //= s
        coords = list(reversed(coords))
        key = tuple(inv[dims[i]][coords[i]] for i in range(len(dims)))
        out[key] = val
    return dims, out, d.get("updated")

print("1/5 Eurostat online buyers (12m)...", file=sys.stderr)
dims, vals, ib20_updated = eurostat("isoc_ec_ib20", [("unit","PC_IND"),("ind_type","IND_TOTAL"),("indic_is","I_BLT12"),("time","2025"),("time","2024")])
gi, ti = dims.index("geo"), dims.index("time")
online = {}
for k,v in vals.items():
    geo, year = k[gi], k[ti]
    if geo in COUNTRIES and (geo not in online or year > online[geo][1]):
        online[geo] = (v, year)
eu27_online = next((v for k,v in vals.items() if k[gi]=="EU27_2020" and k[ti]=="2025"), None)

print("2/5 Eurostat cross-border buyers (2023 final)...", file=sys.stderr)
dims, vals, ibos_updated = eurostat("isoc_ec_ibos", [("unit","PC_IND"),("ind_type","IND_TOTAL"),("indic_is","I_BPG_FOR"),("indic_is","I_BPG_EU"),("indic_is","I_BPG_WRLD"),("indic_is","I_BPG_DOM"),("time","2023")])
gi, ii = dims.index("geo"), dims.index("indic_is")
xb = {}
for k,v in vals.items():
    xb.setdefault(k[gi], {})[k[ii]] = v

print("3/5 World Bank population 15-64 and GDP per capita...", file=sys.stderr)
iso3 = ";".join(v[1] for v in COUNTRIES.values())
def wb(ind):
    d = get_json(f"https://api.worldbank.org/v2/country/{iso3}/indicator/{ind}?format=json&date=2022:2024&per_page=200")
    best = {}
    for row in d[1]:
        c = row["countryiso3code"]; y = row["date"]; v = row["value"]
        if v is None: continue
        if c not in best or y > best[c][1]: best[c] = (v, y)
    return best
pop = wb("SP.POP.1564.TO"); gdp = wb("NY.GDP.PCAP.CD")

print("4/5 ECB reference rates via Frankfurter (GBP base, 12m)...", file=sys.stderr)
symbols = sorted({v[2] for v in COUNTRIES.values()} - {"GBP"})
fx = get_json(f"https://api.frankfurter.dev/v1/{ONE_YEAR_AGO}..{TODAY}?base=GBP&symbols={','.join(symbols)}")
dates = sorted(fx["rates"].keys())
fx_out = {}
for s in symbols:
    series = [(d, fx["rates"][d][s]) for d in dates if s in fx["rates"][d]]
    rates = [r for _,r in series]
    logret = [math.log(rates[i]/rates[i-1]) for i in range(1,len(rates))]
    vol = statistics.pstdev(logret) * math.sqrt(252) * 100
    chg = (rates[-1]/rates[0]-1)*100
    hi, lo = max(rates), min(rates)
    # ~52 weekly points for the sparkline
    step = max(1, len(series)//52)
    fx_out[s] = {"latest": rates[-1], "latest_date": series[-1][0], "start": rates[0],
                 "change_12m_pct": round(chg,2), "vol_annualised_pct": round(vol,2),
                 "range_pct": round((hi-lo)/lo*100,2),
                 "spark": [round(r,4) for _,r in series[::step]]}

print("5/5 VAT standard rates (ibericode/vat-rates)...", file=sys.stderr)
vat_raw = get_json("https://raw.githubusercontent.com/ibericode/vat-rates/master/vat-rates.json")
vat_commit = get_json("https://api.github.com/repos/ibericode/vat-rates/commits?per_page=1")[0]["commit"]["committer"]["date"][:10]
def vat_for(code):
    key = {"EL":"GR","UK":"GB"}.get(code, code)
    entries = vat_raw["items"].get(key)
    if not entries: return None, None
    cur = [e for e in entries if e["effective_from"] <= str(TODAY)]
    e = max(cur, key=lambda e: e["effective_from"])
    return e["rates"].get("standard"), e["effective_from"]

rows = []
for code,(name,i3,cur) in COUNTRIES.items():
    o = online.get(code); x = xb.get(code, {})
    p = pop.get(i3); g = gdp.get(i3)
    vat, vat_from = vat_for(code)
    addressable = (p[0]*o[0]/100) if (p and o) else None
    xb_for = x.get("I_BPG_FOR")
    xb_shoppers = (p[0]*xb_for/100) if (p and xb_for is not None) else None
    rows.append({
        "code": code, "name": name, "iso3": i3, "currency": cur, "eu": code in EU27,
        "online_12m_pct": o[0] if o else None, "online_year": o[1] if o else None,
        "xb_any_pct": xb_for, "xb_eu_pct": x.get("I_BPG_EU"), "xb_world_pct": x.get("I_BPG_WRLD"),
        "dom_pct": x.get("I_BPG_DOM"),
        "pop_15_64": p[0] if p else None, "pop_year": p[1] if p else None,
        "gdp_pc_usd": round(g[0]) if g else None,
        "vat_standard": vat, "vat_from": vat_from,
        "online_shoppers": round(addressable) if addressable else None,
        "xb_shoppers": round(xb_shoppers) if xb_shoppers else None,
        "fx_vol": fx_out[cur]["vol_annualised_pct"] if cur in fx_out else 0.0,
    })

data = {
    "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "eu27_online_12m_pct": eu27_online,
    "countries": rows,
    "fx": fx_out,
    "sources": {
        "eurostat_ib20": {"url":"https://ec.europa.eu/eurostat/databrowser/view/isoc_ec_ib20/", "updated": ib20_updated},
        "eurostat_ibos": {"url":"https://ec.europa.eu/eurostat/databrowser/view/isoc_ec_ibos/", "updated": ibos_updated, "note":"series ends 2023"},
        "worldbank": {"url":"https://api.worldbank.org/v2/country/all/indicator/SP.POP.1564.TO?format=json"},
        "frankfurter": {"url":"https://api.frankfurter.dev/v1/", "note":"ECB reference rates, GBP base", "window": f"{ONE_YEAR_AGO} to {TODAY}"},
        "vat": {"url":"https://github.com/ibericode/vat-rates", "last_commit": vat_commit, "note":"community-maintained; verify against EC Taxes in Europe DB before relying on it"},
    },
}
json.dump(data, open("data.json","w"), indent=1)
print(f"data.json written: {len(rows)} countries, {len(fx_out)} currencies", file=sys.stderr)

# Build the single-file page if the template exists
try:
    tpl = open("index.template.html").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, separators=(",",":")))
    open("index.html","w").write(html)
    print("index.html built", file=sys.stderr)
except FileNotFoundError:
    print("index.template.html not found; skipped page build", file=sys.stderr)
