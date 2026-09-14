#!/usr/bin/env python3
"""
EU Market Entry Radar - data fetch + page build.

Pulls public, no-auth feeds and writes data.json, then injects that JSON
into index.template.html to produce index.html (single self-contained file).

Feeds
  1. Eurostat isoc_ec_ib20  - % of individuals (16-74) who bought online in the last 12 months
  2. Eurostat isoc_ec_ibos  - % of individuals who bought from sellers in other countries (last 3 months, 2023 = final year)
  3. World Bank SP.POP.1564.TO, NY.GDP.PCAP.CD - working-age population and GDP per capita
  4. ECB reference rates: Frankfurter first, ECB Data Portal as fallback - GBP vs each non-euro currency, last 12 months
  5. ibericode/vat-rates - standard VAT rate by country (community-maintained, dated entries)

Resilience: every request retries with backoff. If a feed still fails, that
section is carried over from the previous data.json and the page shows a
warning naming the stale section, so a flaky API never takes the site down.

Run:  python3 build.py
"""
import json, math, sys, time, datetime as dt, urllib.request, urllib.error, statistics, os

TODAY = dt.date.today()
ONE_YEAR_AGO = TODAY - dt.timedelta(days=365)
WARNINGS = []

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

def log(msg): print(msg, file=sys.stderr)

def get_json(url, attempts=4):
    """GET a JSON document with exponential backoff (2, 4, 8 s)."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "market-radar/1.0 (+github.com/mikkaarumugam)", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            if i < attempts - 1:
                wait = 2 ** (i + 1)
                log(f"   retry {i+1}/{attempts-1} after {type(e).__name__}: {e} (sleeping {wait}s)")
                time.sleep(wait)
    raise last

# Previous build, used as last-known-good if a feed is down
PREVIOUS = {}
if os.path.exists("data.json"):
    try:
        PREVIOUS = json.load(open("data.json"))
    except Exception:
        PREVIOUS = {}

def section(name, fn, fallback):
    """Run a feed; on failure, carry over the previous value and record a warning."""
    try:
        return fn()
    except Exception as e:
        log(f"   FAILED {name}: {type(e).__name__}: {e}")
        if fallback is not None:
            WARNINGS.append(f"{name} could not be refreshed on {TODAY}; showing the previous build's values.")
            return fallback
        raise

# ---------------------------------------------------------------- Eurostat
def eurostat(dataset, params):
    base = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
    q = "&".join(f"{k}={v}" for k, v in params) + "&format=JSON&lang=EN"
    d = get_json(f"{base}?{q}")
    dims = d["id"]; size = d["size"]
    idx = {k: d["dimension"][k]["category"]["index"] for k in dims}
    inv = {k: {v: kk for kk, v in idx[k].items()} for k in dims}
    out = {}
    for flat, val in d["value"].items():
        flat = int(flat); coords = []
        for s in reversed(size):
            coords.append(flat % s); flat //= s
        coords = list(reversed(coords))
        out[tuple(inv[dims[i]][coords[i]] for i in range(len(dims)))] = val
    return dims, out, d.get("updated")

def fetch_online():
    dims, vals, updated = eurostat("isoc_ec_ib20", [("unit","PC_IND"),("ind_type","IND_TOTAL"),("indic_is","I_BLT12"),("time","2025"),("time","2024"),("time","2026")])
    gi, ti = dims.index("geo"), dims.index("time")
    online = {}
    for k, v in vals.items():
        geo, year = k[gi], k[ti]
        if geo in COUNTRIES and (geo not in online or year > online[geo][1]):
            online[geo] = (v, year)
    eu = {k[ti]: v for k, v in vals.items() if k[gi] == "EU27_2020"}
    eu27 = eu[max(eu)] if eu else None
    return {"online": online, "eu27": eu27, "updated": updated}

def fetch_xb():
    dims, vals, updated = eurostat("isoc_ec_ibos", [("unit","PC_IND"),("ind_type","IND_TOTAL"),("indic_is","I_BPG_FOR"),("indic_is","I_BPG_EU"),("indic_is","I_BPG_WRLD"),("indic_is","I_BPG_DOM"),("time","2023")])
    gi, ii = dims.index("geo"), dims.index("indic_is")
    xb = {}
    for k, v in vals.items():
        xb.setdefault(k[gi], {})[k[ii]] = v
    return {"xb": xb, "updated": updated}

# ---------------------------------------------------------------- World Bank
def fetch_wb():
    iso3 = ";".join(v[1] for v in COUNTRIES.values())
    def one(ind):
        d = get_json(f"https://api.worldbank.org/v2/country/{iso3}/indicator/{ind}?format=json&date=2022:2025&per_page=200")
        best = {}
        for row in d[1]:
            c, y, v = row["countryiso3code"], row["date"], row["value"]
            if v is None: continue
            if c not in best or y > best[c][1]: best[c] = (v, y)
        return best
    return {"pop": one("SP.POP.1564.TO"), "gdp": one("NY.GDP.PCAP.CD")}

# ---------------------------------------------------------------- FX
SYMBOLS = sorted({v[2] for v in COUNTRIES.values()} - {"GBP"})

def fx_from_frankfurter():
    fx = get_json(f"https://api.frankfurter.dev/v1/{ONE_YEAR_AGO}..{TODAY}?base=GBP&symbols={','.join(SYMBOLS)}")
    series = {s: [] for s in SYMBOLS}
    for d in sorted(fx["rates"]):
        for s in SYMBOLS:
            if s in fx["rates"][d]: series[s].append((d, fx["rates"][d][s]))
    return series, "ECB reference rates via Frankfurter"

def fx_from_ecb():
    # ECB publishes X per EUR. GBP-based rate for X = (X per EUR) / (GBP per EUR).
    keys = "+".join(["GBP"] + [s for s in SYMBOLS if s != "EUR"])
    d = get_json(f"https://data-api.ecb.europa.eu/service/data/EXR/D.{keys}.EUR.SP00.A?format=jsondata&startPeriod={ONE_YEAR_AGO}&endPeriod={TODAY}")
    cur_vals = [v["id"] for v in d["structure"]["dimensions"]["series"][1]["values"]]
    dates = [v["id"] for v in d["structure"]["dimensions"]["observation"][0]["values"]]
    per_eur = {}
    for key, s in d["dataSets"][0]["series"].items():
        cur = cur_vals[int(key.split(":")[1])]
        per_eur[cur] = {dates[int(i)]: obs[0] for i, obs in s["observations"].items() if obs[0] is not None}
    gbp = per_eur["GBP"]
    series = {}
    for s in SYMBOLS:
        pts = []
        for day in sorted(gbp):
            if s == "EUR":
                pts.append((day, 1.0 / gbp[day]))
            elif day in per_eur.get(s, {}):
                pts.append((day, per_eur[s][day] / gbp[day]))
        series[s] = pts
    return series, "ECB Data Portal (EXR), cross-rated to GBP"

def fetch_fx():
    try:
        series, src = fx_from_frankfurter()
    except Exception as e:
        log(f"   Frankfurter failed ({type(e).__name__}); using ECB Data Portal")
        series, src = fx_from_ecb()
    out = {}
    for s in SYMBOLS:
        pts = series[s]
        if len(pts) < 30:
            raise RuntimeError(f"too few FX observations for {s}: {len(pts)}")
        rates = [r for _, r in pts]
        logret = [math.log(rates[i] / rates[i-1]) for i in range(1, len(rates))]
        vol = statistics.pstdev(logret) * math.sqrt(252) * 100
        step = max(1, len(pts) // 52)
        out[s] = {"latest": rates[-1], "latest_date": pts[-1][0], "start": rates[0],
                  "change_12m_pct": round((rates[-1] / rates[0] - 1) * 100, 2),
                  "vol_annualised_pct": round(vol, 2),
                  "range_pct": round((max(rates) - min(rates)) / min(rates) * 100, 2),
                  "spark": [round(r, 4) for _, r in pts[::step]]}
    return {"fx": out, "source": src}

# ---------------------------------------------------------------- VAT
def fetch_vat():
    raw = get_json("https://raw.githubusercontent.com/ibericode/vat-rates/master/vat-rates.json")
    try:
        commit = get_json("https://api.github.com/repos/ibericode/vat-rates/commits?per_page=1")[0]["commit"]["committer"]["date"][:10]
    except Exception:
        commit = PREVIOUS.get("sources", {}).get("vat", {}).get("last_commit", "unknown")
    out = {}
    for code in COUNTRIES:
        key = {"EL": "GR", "UK": "GB"}.get(code, code)
        entries = raw["items"].get(key)
        if not entries:
            out[code] = (None, None); continue
        cur = [e for e in entries if e["effective_from"] <= str(TODAY)]
        e = max(cur, key=lambda e: e["effective_from"])
        out[code] = (e["rates"].get("standard"), e["effective_from"])
    return {"vat": out, "commit": commit}

# ---------------------------------------------------------------- run
def prev_section(builder):
    """Rebuild a section's inputs from the previous data.json so a failed feed can be carried over."""
    try:
        return builder(PREVIOUS)
    except Exception:
        return None

log("1/5 Eurostat online buyers (12m)...")
ONLINE = section("Eurostat online buyers", fetch_online, prev_section(lambda p: {
    "online": {r["code"]: (r["online_12m_pct"], r["online_year"]) for r in p["countries"] if r["online_12m_pct"] is not None},
    "eu27": p["eu27_online_12m_pct"], "updated": p["sources"]["eurostat_ib20"]["updated"]}))
log("2/5 Eurostat cross-border buyers (2023 final)...")
XB = section("Eurostat cross-border buyers", fetch_xb, prev_section(lambda p: {
    "xb": {r["code"]: {"I_BPG_FOR": r["xb_any_pct"], "I_BPG_EU": r["xb_eu_pct"], "I_BPG_WRLD": r["xb_world_pct"], "I_BPG_DOM": r["dom_pct"]} for r in p["countries"]},
    "updated": p["sources"]["eurostat_ibos"]["updated"]}))
log("3/5 World Bank population 15-64 and GDP per capita...")
WB = section("World Bank", fetch_wb, prev_section(lambda p: {
    "pop": {r["iso3"]: (r["pop_15_64"], r["pop_year"]) for r in p["countries"] if r["pop_15_64"]},
    "gdp": {r["iso3"]: (r["gdp_pc_usd"], r["pop_year"]) for r in p["countries"] if r["gdp_pc_usd"]}}))
log("4/5 ECB reference rates (GBP base, 12m)...")
FX = section("ECB exchange rates", fetch_fx, prev_section(lambda p: {"fx": p["fx"], "source": p["sources"]["frankfurter"]["note"]}))
log("5/5 VAT standard rates (ibericode/vat-rates)...")
VAT = section("VAT rates", fetch_vat, prev_section(lambda p: {
    "vat": {r["code"]: (r["vat_standard"], r["vat_from"]) for r in p["countries"]}, "commit": p["sources"]["vat"]["last_commit"]}))

rows = []
for code, (name, i3, cur) in COUNTRIES.items():
    o = ONLINE["online"].get(code); x = XB["xb"].get(code, {})
    p = WB["pop"].get(i3); g = WB["gdp"].get(i3)
    vat, vat_from = VAT["vat"].get(code, (None, None))
    xb_for = x.get("I_BPG_FOR")
    rows.append({
        "code": code, "name": name, "iso3": i3, "currency": cur, "eu": code in EU27,
        "online_12m_pct": o[0] if o else None, "online_year": o[1] if o else None,
        "xb_any_pct": xb_for, "xb_eu_pct": x.get("I_BPG_EU"), "xb_world_pct": x.get("I_BPG_WRLD"), "dom_pct": x.get("I_BPG_DOM"),
        "pop_15_64": p[0] if p else None, "pop_year": p[1] if p else None,
        "gdp_pc_usd": round(g[0]) if g else None,
        "vat_standard": vat, "vat_from": vat_from,
        "online_shoppers": round(p[0] * o[0] / 100) if (p and o) else None,
        "xb_shoppers": round(p[0] * xb_for / 100) if (p and xb_for is not None) else None,
        "fx_vol": FX["fx"][cur]["vol_annualised_pct"] if cur in FX["fx"] else 0.0,
    })

data = {
    "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "eu27_online_12m_pct": ONLINE["eu27"],
    "countries": rows,
    "fx": FX["fx"],
    "warnings": WARNINGS,
    "sources": {
        "eurostat_ib20": {"url": "https://ec.europa.eu/eurostat/databrowser/view/isoc_ec_ib20/", "updated": ONLINE["updated"]},
        "eurostat_ibos": {"url": "https://ec.europa.eu/eurostat/databrowser/view/isoc_ec_ibos/", "updated": XB["updated"], "note": "series ends 2023"},
        "worldbank": {"url": "https://api.worldbank.org/v2/country/all/indicator/SP.POP.1564.TO?format=json"},
        "frankfurter": {"url": "https://api.frankfurter.dev/v1/", "note": FX["source"], "window": f"{ONE_YEAR_AGO} to {TODAY}"},
        "vat": {"url": "https://github.com/ibericode/vat-rates", "last_commit": VAT["commit"], "note": "community-maintained; verify against EC Taxes in Europe DB before relying on it"},
    },
}
json.dump(data, open("data.json", "w"), indent=1)
log(f"data.json written: {len(rows)} countries, {len(FX['fx'])} currencies, {len(WARNINGS)} warning(s)")
for w in WARNINGS: log("   WARNING " + w)

try:
    tpl = open("index.template.html").read()
    open("index.html", "w").write(tpl.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))))
    log("index.html built")
except FileNotFoundError:
    log("index.template.html not found; skipped page build")
