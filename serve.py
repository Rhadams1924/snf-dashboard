#!/usr/bin/env python3
"""
SNF Data Dashboard — Local Proxy Server
Enables multi-state data loading by proxying CMS API requests (bypassing CORS).

Usage:
    python3 serve.py
Then open: http://localhost:8080/dashboard.html
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8080

# ── CMS API endpoints ────────────────────────────────────────────────────────
DKAN = "https://data.cms.gov/provider-data/api/1/datastore/query/4pq5-n9py/0"
DATA_API = "https://data.cms.gov/data-api/v1/dataset/{id}/data"
LTC_ID   = "129a6503-c0f1-4132-b186-4c0232c2d894"
PBJ_ID   = "7e0d53ba-8f02-4c66-98a5-14a1c997c50d"

# In-memory cache: state_code → processed data dict
CACHE = {}

# ── Helpers ──────────────────────────────────────────────────────────────────
def sf(v):
    try: return float(v or 0)
    except: return 0.0

def si(v):
    try: return int(float(v or 0))
    except: return 0

def api_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 ** attempt)

# ── Fetchers ─────────────────────────────────────────────────────────────────
def fetch_provider(state):
    """Paginate DKAN Provider Information for a state."""
    records, offset = [], 0
    while True:
        params = urllib.parse.urlencode({
            "conditions[0][property]": "state",
            "conditions[0][value]": state,
            "conditions[0][operator]": "=",
            "limit": 500, "offset": offset,
        })
        data = api_get(f"{DKAN}?{params}")
        batch = data.get("results", [])
        records.extend(batch)
        offset += len(batch)
        if offset >= data.get("count", 0) or not batch:
            break
    return records

def fetch_data_api(dataset_id, state_field, state):
    """Paginate a data-api/v1 dataset filtered by state."""
    records, offset = [], 0
    while True:
        params = urllib.parse.urlencode({f"filter[{state_field}]": state,
                                         "size": 1500, "offset": offset})
        batch = api_get(f"{DATA_API.format(id=dataset_id)}?{params}")
        if not isinstance(batch, list) or not batch:
            break
        records.extend(batch)
        if len(batch) < 1500:
            break
        offset += 1500
    return records

def fetch_pbj_aggregated(state):
    """Fetch PBJ daily nurse staffing, aggregating census by date (avoids storing raw rows)."""
    daily, offset = defaultdict(float), 0
    pages = 0
    while True:
        params = urllib.parse.urlencode({"filter[STATE]": state,
                                         "size": 1500, "offset": offset})
        batch = api_get(f"{DATA_API.format(id=PBJ_ID)}?{params}")
        if not isinstance(batch, list) or not batch:
            break
        for r in batch:
            try:
                daily[r["WorkDate"]] += sf(r.get("MDScensus"))
            except Exception:
                pass
        pages += 1
        if pages % 5 == 0:
            print(f"    PBJ page {pages} ({offset + len(batch):,} rows)...")
        if len(batch) < 1500:
            break
        offset += 1500
    return [{"date": k, "census": round(v)} for k, v in sorted(daily.items())]

# ── Data processor ───────────────────────────────────────────────────────────
def build_state_data(state):
    t0 = time.time()
    print(f"[{state}] Fetching provider info...", flush=True)
    provider = fetch_provider(state)
    print(f"[{state}] {len(provider)} facilities. Fetching LTC characteristics...", flush=True)
    ltc_raw = fetch_data_api(LTC_ID, "State", state)
    print(f"[{state}] Fetching PBJ daily census (may take a moment for large states)...", flush=True)
    daily_series = fetch_pbj_aggregated(state)
    print(f"[{state}] Done in {time.time()-t0:.1f}s", flush=True)

    ltc_by_ccn = {r.get("Provider Number", ""): r for r in ltc_raw}

    facilities = []
    for p in provider:
        ccn = p.get("cms_certification_number_ccn", "")
        l = ltc_by_ccn.get(ccn, {})
        beds = si(p.get("number_of_certified_beds", 0))
        avg_res = sf(p.get("average_number_of_residents_per_day", 0))
        facilities.append({
            "ccn":           ccn,
            "name":          p.get("provider_name", ""),
            "address":       p.get("provider_address", ""),
            "city":          p.get("citytown", ""),
            "county":        p.get("countyparish", ""),
            "zip":           p.get("zip_code", ""),
            "phone":         p.get("telephone_number", ""),
            "beds":          beds,
            "avg_residents": round(avg_res, 1),
            "occupancy_pct": round(100 * avg_res / beds, 1) if beds > 0 else None,
            "ownership":     p.get("ownership_type", ""),
            "chain":         p.get("chain_name", ""),
            "urban":         p.get("urban", ""),
            "overall_rating":  si(p.get("overall_rating", 0)),
            "health_rating":   si(p.get("health_inspection_rating", 0)),
            "staffing_rating": si(p.get("staffing_rating", 0)),
            "qm_rating":       si(p.get("qm_rating", 0)),
            "total_hprd":  sf(p.get("reported_total_nurse_staffing_hours_per_resident_per_day", 0)),
            "rn_hprd":     sf(p.get("reported_rn_staffing_hours_per_resident_per_day", 0)),
            "turnover":    sf(p.get("total_nursing_staff_turnover", 0)),
            "rn_turnover": sf(p.get("registered_nurse_turnover", 0)),
            "special_focus": bool(p.get("special_focus_status", "")),
            "abuse_icon":    p.get("abuse_icon") == "Y",
            "lat": sf(p.get("latitude", 0)),
            "lon": sf(p.get("longitude", 0)),
            "ccrc":           p.get("continuing_care_retirement_community") == "Y",
            "medicare_census": si(l.get("Medicare Census", 0)),
            "medicaid_census": si(l.get("Medicaid Census", 0)),
            "other_census":    si(l.get("Other Census", 0)),
            "alz_beds":     si(l.get("Number of Alzheimer's Disease Beds", 0)),
            "vent_beds":    si(l.get("Number of Ventilator Beds", 0)),
            "hospice_beds": si(l.get("Number of Hospice Beds", 0)),
            "fines":       si(p.get("number_of_fines", 0)),
            "fine_amount": sf(p.get("total_amount_of_fines_in_dollars", 0)),
            "penalties":   si(p.get("total_number_of_penalties", 0)),
            "deficiencies": si(p.get("rating_cycle_1_total_number_of_health_deficiencies", 0)),
            "date_approved": p.get("date_first_approved_to_provide_medicare_and_medicaid_services", ""),
        })

    total_beds = sum(f["beds"] for f in facilities)
    total_res  = sum(f["avg_residents"] for f in facilities)
    rated  = [f for f in facilities if f["overall_rating"] > 0]
    hprd_f = [f for f in facilities if f["total_hprd"] > 0]
    turn_f = [f for f in facilities if f["turnover"] > 0]

    kpis = {
        "total_facilities":  len(facilities),
        "total_beds":        total_beds,
        "avg_daily_census":  round(total_res),
        "occupancy_rate":    round(100 * total_res / total_beds, 1) if total_beds else 0,
        "avg_star_rating":   round(sum(f["overall_rating"] for f in rated) / len(rated), 2) if rated else 0,
        "for_profit_pct":    round(100 * sum(1 for f in facilities if "For profit" in f["ownership"]) / len(facilities), 1) if facilities else 0,
        "special_focus_count": sum(1 for f in facilities if f["special_focus"]),
        "avg_hprd":    round(sum(f["total_hprd"] for f in hprd_f) / len(hprd_f), 2) if hprd_f else 0,
        "avg_turnover": round(sum(f["turnover"] for f in turn_f) / len(turn_f), 1) if turn_f else 0,
    }

    return {"facilities": facilities, "kpis": kpis, "daily_series": daily_series}

# ── HTTP Handler ─────────────────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/state/"):
            raw = self.path.split("/")[-1].split("?")[0].upper()
            state = raw[:2] if raw.isalpha() else ""
            if not state:
                self.send_error(400, "Invalid state code"); return
            if state not in CACHE:
                try:
                    CACHE[state] = build_state_data(state)
                except Exception as e:
                    print(f"  ERROR for {state}: {e}", flush=True)
                    self.send_error(500, str(e)); return
            body = json.dumps(CACHE[state]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "/api/" in msg or "200" not in msg:
            print(f"  {msg}", flush=True)

# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\n🏥  SNF Dashboard Server running")
    print(f"    Open → http://localhost:{PORT}/dashboard.html")
    print(f"    Stop → Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
