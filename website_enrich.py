import argparse
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd
from lxml import html as lhtml

from rr_roles import (
    PRIORITY_LABEL,
    split_name,
    title_allowed,
    title_priority,
)
from auto_enrich import (
    EMAIL_RE,
    FINAL_COLS,
    MAX_CONTACTS_PER_FIRM,
    clean_text,
    dedupe,
    normalize_domain,
    normalize_firm,
)

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TEAM_HINTS = re.compile(
    r"team|people|about|founder|leadership|invest|partner|staff|bio|firm",
    re.I,
)

NAME_RE = re.compile(r"^([A-Z][\w'’.-]*(?: [A-Z][\w'’.-]*){1,3})$")

SEP_SPLIT_RE = re.compile(r"\s*[|•·\t]|\s{2,}|\s+-\s+|\s+–\s+")

OBFUSCATION_FIXES = [
    (re.compile(r"\s*\[\s*at\s*\]\s*", re.I), "@"),
    (re.compile(r"\s*\(\s*at\s*\)\s*", re.I), "@"),
    (re.compile(r"\s+at\s+", re.I), "@"),
    (re.compile(r"\s*\[\s*dot\s*\]\s*", re.I), "."),
    (re.compile(r"\s*\(\s*dot\s*\)\s*", re.I), "."),
    (re.compile(r"\s+dot\s+", re.I), "."),
]

MAX_PAGES_DEFAULT = 8


def fetch(url, timeout=15):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and ctype:
                return ""
            return resp.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return ""


def same_site(url, domain):
    host = urllib.parse.urlparse(url).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    core = domain.split(".")
    registrable = ".".join(core[-2:]) if len(core) >= 2 else domain
    return host == registrable or host.endswith("." + registrable)


def extract_links(html_text, base_url):
    try:
        doc = lhtml.fromstring(html_text)
    except lhtml.LXMLError:
        return []
    links = []
    for a in doc.iter("a"):
        href = a.get("href") or ""
        text = " ".join(a.text_content().split())
        if not href.startswith(("http", "/", "#", "mailto")):
            continue
        if href.startswith("#") or href.startswith("mailto"):
            continue
        absu = urllib.parse.urljoin(base_url, href)
        if urllib.parse.urlparse(absu).scheme not in ("http", "https"):
            continue
        links.append((absu, text))
    return links


def html_to_lines(raw):
    raw = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ",
                 raw, flags=re.S | re.I)
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    raw = re.sub(r"<[^>]+>", "\n", raw)
    raw = raw.replace("&amp;", "&").replace("&nbsp;", " ")
    lines = []
    for ln in raw.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip(" |•·-–—")
        if ln:
            lines.append(ln)
    return lines


def find_emails(lines):
    hits = {}
    for i, ln in enumerate(lines):
        found = EMAIL_RE.findall(ln)
        if found:
            hits[i] = [e.lower() for e in found]
            continue
        fixed = ln
        for pat, repl in OBFUSCATION_FIXES:
            fixed = pat.sub(repl, fixed)
        for m in EMAIL_RE.findall(fixed):
            hits[i] = [m.lower()]
    return hits


GENERIC_EMAIL_PREFIXES = {
    "info", "contact", "support", "hello", "admin", "office", "team",
    "press", "media", "marketing", "sales", "careers", "jobs", "ir",
    "general", "enquiries", "inquiries",
}


EMAIL_ATTACH_WINDOW = 6


def pair_names_titles(lines, email_hits):
    candidates = []

    def make(name_ln, title_ln, line_no):
        title = title_ln.strip(" ,;-–—")
        company = ""
        m = re.search(
            r"\b(?:at|@)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})",
            title,
        )
        if m:
            company = m.group(1)
            title = title[: m.start()].strip(" ,-")
        first, last = split_name(name_ln.strip())
        return {
            "first_name": first,
            "last_name": last,
            "contact_title": title,
            "company_hint": company,
            "primary_email": "",
            "line": line_no,
        }

    def title_line(ln):
        return title_allowed(ln) and title_priority(ln) is not None

    prev_name = ""
    for i, ln in enumerate(lines):
        chunks = [c.strip(" ,;-–—") for c in SEP_SPLIT_RE.split(ln) if c.strip()]
        done = False
        for j, c in enumerate(chunks):
            if title_line(c) and j > 0 and NAME_RE.match(chunks[j - 1]):
                candidates.append(make(chunks[j - 1], c, i))
                done = True
                break
        if done:
            prev_name = ""
            continue
        if title_line(ln):
            if prev_name and NAME_RE.match(prev_name):
                candidates.append(make(prev_name, ln, i))
                prev_name = ""
            continue
        prev_name = ln if NAME_RE.match(ln) else ""

    for i, ln in enumerate(lines):
        emails = email_hits.get(i)
        if not emails:
            continue
        email = emails[0]
        local = email.split("@")[0].lower()
        if local in GENERIC_EMAIL_PREFIXES:
            continue
        best = None
        for cand in candidates:
            if cand["line"] >= i:
                continue
            if cand["primary_email"]:
                continue
            if best is None or cand["line"] > best["line"]:
                best = cand
        if best is not None and i - best["line"] <= EMAIL_ATTACH_WINDOW:
            best["primary_email"] = email
    return candidates


def extract_people(raw):
    raw = re.sub(
        r"<a\b[^>]*href=[\"']mailto:([^\"'?]+)[^>]*>",
        lambda m: "<div>" + m.group(1) + "</div>",
        raw,
        flags=re.I,
    )
    lines = html_to_lines(raw)
    email_hits = find_emails(lines)
    cands = pair_names_titles(lines, email_hits)

    seen = set()
    out = []
    for c in cands:
        prio = title_priority(c["contact_title"])
        if prio is None or not title_allowed(c["contact_title"]):
            continue
        key = (c["first_name"].lower(), c["last_name"].lower())
        if key in seen:
            for prev in out:
                if (prev["first_name"].lower(),
                        prev["last_name"].lower()) == key:
                    if c["primary_email"] and not prev["primary_email"]:
                        prev["primary_email"] = c["primary_email"]
                    break
            continue
        seen.add(key)
        c["priority"] = prio
        out.append(c)
    return out


def enrich_site(domain, delay, max_pages):
    base = f"https://{domain}"
    queue = [(base, 0)]
    visited = set()
    people = {}

    team_page_visited = False
    reachable = False
    while queue and len(visited) < max_pages:
        url, depth = queue.pop(0)
        norm = url.split("#")[0].rstrip("/")
        if norm in visited:
            continue
        visited.add(norm)
        raw = fetch(norm)
        time.sleep(delay)
        if not raw:
            continue
        reachable = True

        found = extract_people(raw)
        for p in found:
            k = (p["first_name"].lower(), p["last_name"].lower())
            existing = people.get(k)
            if existing is None:
                people[k] = p
            elif p["primary_email"] and not existing["primary_email"]:
                existing["primary_email"] = p["primary_email"]

        if any(p["primary_email"] for p in people.values()) and depth >= 1:
            break
        if depth < 2:
            for link_url, text in extract_links(raw, norm):
                if not same_site(link_url, domain):
                    continue
                if not TEAM_HINTS.search(link_url) and \
                   not TEAM_HINTS.search(text):
                    continue
                queue.append((link_url.split("#")[0], depth + 1))

    ranked = sorted(
        people.values(),
        key=lambda p: (p["priority"],
                       0 if p["primary_email"] else 1),
    )
    return ranked[:MAX_CONTACTS_PER_FIRM], reachable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="final_workbook.xlsx")
    ap.add_argument("--delay", type=float, default=0.7,
                    help="seconds between requests to the same site")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT)
    args = ap.parse_args()

    df = pd.read_excel(args.input)
    df = df[df["vc_name"].notna()].reset_index(drop=True)
    firms = []
    for _, r in df.iterrows():
        vc = clean_text(r["vc_name"])
        dom = normalize_domain(r.get("website"))
        if not vc and not dom:
            continue
        firms.append({
            "vc_name": vc,
            "website": clean_text(r.get("website")),
            "normalized_firm_name": normalize_firm(vc),
            "normalized_domain": dom,
        })
    firms = dedupe(firms)
    print(f"{len(firms)} unique firms")

    all_rows = []
    no_matches = []
    for i, firm in enumerate(firms, 1):
        label = firm["vc_name"] or firm["normalized_domain"]
        print(f"[{i}/{len(firms)}] {label}")
        if not firm["normalized_domain"]:
            no_matches.append({
                "vc_name": firm["vc_name"], "website": firm["website"],
                "normalized_domain": "", "lookup_status": "no_match",
                "notes": "no valid website to crawl",
            })
            print("    no_match (no valid website)")
            continue

        picks, reachable = enrich_site(
            firm["normalized_domain"], args.delay, args.max_pages
        )
        if not picks:
            note = ("site unreachable" if not reachable
                    else "no role-matching contacts published on site")
            no_matches.append({
                "vc_name": firm["vc_name"], "website": firm["website"],
                "normalized_domain": firm["normalized_domain"],
                "lookup_status": "no_match", "notes": note,
            })
            print(f"    no_match ({note})")
            continue

        for pos, p in enumerate(picks, 1):
            conf = 0.35 + (0.25 if p["priority"] <= 2 else
                           0.15 if p["priority"] <= 4 else 0.05)
            if p["primary_email"]:
                conf += 0.3
            all_rows.append({
                "vc_name": firm["vc_name"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "primary_email": p["primary_email"],
                "website": firm["website"],
                "normalized_firm_name": firm["normalized_firm_name"],
                "normalized_domain": firm["normalized_domain"],
                "contact_title": p["contact_title"],
                "contact_priority": PRIORITY_LABEL[p["priority"]],
                "source": "vc_website",
                "confidence": round(min(conf, 0.99), 2),
                "lookup_status":
                    "complete" if p["primary_email"] else "found_no_email",
                "notes": f"rank {pos}; from {firm['normalized_domain']}",
            })
            print(f"    {PRIORITY_LABEL[p['priority']]:<18} "
                  f"{p['first_name']} {p['last_name']} "
                  f"<{p['primary_email'] or '-'}>")

    out = pd.DataFrame(all_rows, columns=FINAL_COLS)
    order = {v: k for k, v in PRIORITY_LABEL.items()}
    out["_o"] = out["contact_priority"].map(order).fillna(9)
    out = out.sort_values(["vc_name", "_o"], kind="stable").drop(columns=["_o"])
    nm = pd.DataFrame(no_matches)
    with pd.ExcelWriter(args.output) as xw:
        out.to_excel(xw, sheet_name="contacts", index=False)
        if not nm.empty:
            nm.to_excel(xw, sheet_name="no_match_firms", index=False)

    complete = sum(1 for r in all_rows if r["primary_email"])
    matched = len({r["vc_name"] for r in all_rows})
    print(f"\nwrote {len(out)} rows ({matched} firms matched, "
          f"{complete} emails) -> {args.output}")


if __name__ == "__main__":
    main()
