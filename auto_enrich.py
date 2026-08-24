import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

import pandas as pd

from rr_roles import (
    PRIORITY_LABEL,
    split_name,
    title_allowed,
    title_priority,
)

BASE_URL = "https://api.rocketreach.co/api/v2"
SEARCH_ENDPOINT = f"{BASE_URL}/person/search"
LOOKUP_ENDPOINT = f"{BASE_URL}/person/lookup"

REQUIRED = ["vc_name", "first_name", "last_name", "primary_email", "website"]
FINAL_COLS = [
    "vc_name", "first_name", "last_name", "primary_email",
    "website", "normalized_firm_name", "normalized_domain",
    "contact_title", "contact_priority", "source",
    "confidence", "lookup_status", "notes",
]

SEARCH_TITLES = [
    "managing partner", "general partner", "founding partner",
    "venture partner", "partner", "principal", "investment manager",
    "investment team", "senior associate", "investment associate",
    "associate",
]

MAX_CONTACTS_PER_FIRM = 3

LEGAL_TOKENS = {
    "vc", "ventures", "venture", "capital", "partners", "partner",
    "management", "mgmt", "group", "llc", "lp", "llp", "inc", "co",
    "company", "fund", "the", "and", "&", "investments", "investment",
}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def clean_text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = unicodedata.normalize("NFKC", str(v))
    return re.sub(r"\s+", " ", s).strip()


def normalize_domain(url):
    s = clean_text(url).lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].split(":")[0].strip(".")
    if not re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$", s):
        return ""
    return s


def normalize_firm(name):
    s = clean_text(name).lower()
    s = re.sub(r"[^\w\s&-]", " ", s)
    tokens = [t for t in s.split() if t]
    kept = [t for t in tokens if t not in LEGAL_TOKENS]
    return " ".join(kept) if kept else " ".join(tokens)


def dedupe(rows):
    unique = []
    seen_keys = set()
    for r in rows:
        key = r["normalized_domain"] or r["normalized_firm_name"]
        if key and key in seen_keys:
            continue
        dup = False
        if not key:
            for u in unique:
                if u["normalized_firm_name"] and SequenceMatcher(
                    None, r["normalized_firm_name"], u["normalized_firm_name"]
                ).ratio() >= 0.92:
                    dup = True
                    break
        if dup:
            continue
        seen_keys.add(key)
        unique.append(r)
    return unique


class ApiClient:
    def __init__(self, api_key, delay=1.0):
        self.api_key = api_key
        self.delay = delay
        self.lookups_used = 0
        self.searches_used = 0

    def call(self, method, endpoint, params=None, body=None):
        url = endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method.upper(),
            headers={"Api-Key": self.api_key,
                     "Content-Type": "application/json"},
        )
        last_err = ""
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    time.sleep(self.delay)
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="ignore")[:300]
                if e.code == 429 and attempt < 3:
                    wait = 2 ** (attempt + 2)
                    print(f"    rate limited; sleeping {wait}s")
                    time.sleep(wait)
                    continue
                last_err = f"HTTP {e.code}: {detail}"
                break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = str(e)
                if attempt < 3:
                    time.sleep(2 ** (attempt + 2))
                    continue
        raise RuntimeError(f"API {method} {endpoint} failed: {last_err}")


def search_people(client, firm):
    query = {}
    if firm["normalized_domain"]:
        query["current_employer_domain"] = [firm["normalized_domain"]]
    else:
        query["current_employer"] = [firm["normalized_firm_name"]]
    query["current_title"] = SEARCH_TITLES
    payload = client.call(
        "POST", SEARCH_ENDPOINT,
        body={"query": query, "start": 1, "page_size": 10},
    )
    client.searches_used += 1
    return payload.get("people", [])


def lookup_person(client, person_id):
    payload = client.call("GET", LOOKUP_ENDPOINT, params={"id": person_id})
    client.lookups_used += 1
    return payload


def score_contact(person, firm):
    title = clean_text(person.get("current_title"))
    prio = title_priority(title)
    conf = 0.4 + (0.2 if prio <= 2 else 0.1 if prio <= 4 else 0.0)
    emp_domain = normalize_domain(person.get("current_employer_domain")
                                  or person.get("current_employer") or "")
    core = firm["normalized_domain"].split(".")[0]
    if core and core in (emp_domain or ""):
        conf += 0.1
    return round(min(conf, 0.99), 2), emp_domain


def enrich_firm(client, firm):
    result_rows = []
    try:
        people = search_people(client, firm)
    except RuntimeError as e:
        return [], "api_error", str(e)

    candidates = []
    for p in people:
        title = clean_text(p.get("current_title"))
        if not title_allowed(title):
            continue
        prio = title_priority(title)
        if prio is None:
            continue
        candidates.append({"person": p, "prio": prio})
    candidates.sort(key=lambda c: c["prio"])
    candidates = candidates[:MAX_CONTACTS_PER_FIRM]

    if not candidates:
        return [], "no_match", "no role-matching people in RR search results"

    for cand in candidates:
        p = cand["person"]
        pid = p.get("id")
        first, last = split_name(clean_text(p.get("name")))
        row = {
            "vc_name": firm["vc_name"],
            "first_name": first,
            "last_name": last,
            "primary_email": "",
            "website": firm["website"],
            "normalized_firm_name": firm["normalized_firm_name"],
            "normalized_domain": firm["normalized_domain"],
            "contact_title": clean_text(p.get("current_title")),
            "contact_priority": PRIORITY_LABEL[cand["prio"]],
            "source": "rocketreach_api",
            "confidence": "",
            "lookup_status": "complete",
            "notes": "",
        }
        status = "complete"
        note = ""
        if pid is not None:
            try:
                full = lookup_person(client, pid)
                email = clean_text(full.get("email")).lower()
                smtp = str(full.get("smtp_valid", "")).lower()
                if email and EMAIL_RE.match(email) and smtp != "invalid":
                    row["primary_email"] = email
                elif smtp == "invalid":
                    status, note = "found_no_email", "revealed email invalid"
                else:
                    status, note = "found_no_email", "no email returned by RR"
                title_full = clean_text(full.get("current_title")) or row["contact_title"]
                if title_allowed(title_full):
                    row["contact_title"] = title_full
                new_prio = title_priority(title_full)
                if new_prio is not None:
                    row["contact_priority"] = PRIORITY_LABEL[new_prio]
                    cand["prio"] = new_prio
            except RuntimeError as e:
                status, note = "api_error", str(e)
        conf, _ = score_contact(p, firm)
        if row["primary_email"]:
            conf = min(conf + 0.3, 0.99)
        row["confidence"] = conf
        row["lookup_status"] = status
        row["notes"] = note
        result_rows.append(row)
    return result_rows, "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="final_workbook.xlsx")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--go", action="store_true",
                    help="execute lookups (consumes credits); "
                         "default is a free dry-run plan")
    args = ap.parse_args()

    api_key = os.environ.get("ROCKETREACH_API_KEY", "").strip()
    if args.go and not api_key:
        sys.exit("set ROCKETREACH_API_KEY to run with --go")

    try:
        df = pd.read_excel(args.input)
    except FileNotFoundError:
        sys.exit(f"input file not found: {args.input}")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        sys.exit(f"missing required columns: {missing}")

    df = df[df["vc_name"].notna()].reset_index(drop=True)
    firms = []
    for _, r in df.iterrows():
        vc = clean_text(r["vc_name"])
        firms.append({
            "vc_name": vc,
            "website": clean_text(r.get("website")),
            "normalized_firm_name": normalize_firm(vc),
            "normalized_domain": normalize_domain(r.get("website")),
        })
    firms = dedupe(firms)
    print(f"{len(firms)} unique firms "
          f"({len(df) - len(firms)} duplicates skipped)")

    if not args.go:
        print("\nDRY RUN — no API calls made. Plan:")
        for i, f in enumerate(firms, 1):
            tgt = f["normalized_domain"] or f["normalized_firm_name"]
            print(f"  {i:3d}. {f['vc_name']} -> search {tgt}")
        est = len(firms) * MAX_CONTACTS_PER_FIRM
        print(f"max lookups if all firms match: {est} credits")
        print("run again with --go to execute")
        return

    client = ApiClient(api_key, delay=args.delay)
    all_rows = []
    errors = []
    no_matches = []
    for i, firm in enumerate(firms, 1):
        print(f"[{i}/{len(firms)}] {firm['vc_name']}")
        rows, status, note = enrich_firm(client, firm)
        if status == "api_error":
            errors.append((firm["vc_name"], note))
            print(f"    ERROR: {note}")
            continue
        if status == "no_match":
            no_matches.append({
                "vc_name": firm["vc_name"],
                "website": firm["website"],
                "normalized_domain": firm["normalized_domain"],
                "lookup_status": "no_match",
                "notes": note,
            })
            print(f"    no_match ({note})")
            continue
        for r in rows:
            print(f"    {r['contact_priority']:<18} "
                  f"{r['first_name']} {r['last_name']} "
                  f"<{r['primary_email'] or '-'}>")
        all_rows.extend(rows)

    out = pd.DataFrame(all_rows, columns=FINAL_COLS)
    order = {v: k for k, v in PRIORITY_LABEL.items()}
    out["_o"] = out["contact_priority"].map(order).fillna(9)
    out = out.sort_values(["vc_name", "_o"], kind="stable").drop(columns=["_o"])
    nm = pd.DataFrame(no_matches)
    with pd.ExcelWriter(args.output) as xw:
        out.to_excel(xw, sheet_name="contacts", index=False)
        if not nm.empty:
            nm.to_excel(xw, sheet_name="no_match_firms", index=False)

    matched = len({r["vc_name"] for r in all_rows})
    complete = sum(1 for r in all_rows if r["primary_email"])
    print(f"\nwrote {len(out)} rows ({matched} firms matched, "
          f"{complete} emails) -> {args.output}")
    print(f"credits used: {client.lookups_used} lookups, "
          f"{client.searches_used} searches (searches are usually free)")
    if errors:
        print(f"{len(errors)} firms failed with API errors — rerun later")


if __name__ == "__main__":
    main()
