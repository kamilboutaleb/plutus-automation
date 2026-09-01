import argparse
import os
import re
import sys
import time
import urllib.parse

import pandas as pd
from playwright.sync_api import sync_playwright

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

LOGIN_URL = "https://rocketreach.co/login"
COMPANY_URL = "https://rocketreach.co/company"
PERSON_URL = "https://rocketreach.co/person"

# People-search paging. Title reads are free, so the scan window is much
# wider than the credited email-reveal cap (MAX_CONTACTS_PER_FIRM).
SCAN_PAGE_SIZE = 10
MAX_SCAN_CARDS = 30
DEFAULT_LOGIN_TIMEOUT = 15

# Selectors are centralised here because RocketReach's DOM is not a public,
# stable API. They were verified against the live site when this file was
# written; if RocketReach changes their markup, adjust ONLY this block.
SEL = {
    "login_email": "input[name='email'], input[type='email'], "
                   "input[autocomplete='username']",
    "login_password": "input[name='password'], input[type='password']",
    "login_submit": "button[type='submit']:has-text('log in'), "
                    "button[type='submit']:has-text('sign in'), "
                    "button:has-text('continue')",
    # After login the nav shows the dashboard. Used as a logged-in sentinel.
    "logged_in_marker": "a[href='/dashboard'], "
                        "a[href='/search'], "
                        "[data-testid='bell_icon']",
    # Company search page ("Search Employees" button -> person URL w/ employer id).
    "company_search_employees": "button:has-text('Search Employees'), "
                                "a:has-text('Search Employees')",
    # People search results.
    "result_items": "div.result-items",
    "result_card": "div.result-items > div[data-profile-card-id]",
    "card_name": "p#profile-name",
    "card_title": "p.line-clamp-2.text-sm.font-medium-420",
    "card_employer": "a.text-rr-brand-primary span.text-base.font-heavy-552",
    "card_email": ("a[data-testid='email-phone-text-desktop'] href, "
                   "a[data-testid='email-phone-text-desktop'], "
                   "a[data-testid='email-phone-text-mobile']"),
    # Section that holds a revealed contact email (present when unlocked).
    "card_email_section": "div[data-onboarding-id='main-contact-info-lookup-complete']",
    # The button that reveals email / phone on a profile card. It consumes a
    # lookup credit when clicked.
    "reveal_button": "button:has-text('Get Contact Info'), "
                     "button:has-text('Reveal Email'), "
                     "button:has-text('Get Email'), "
                     "button:has-text('Lookup')",
}

AUTHENTICATED_PATHS = ("/dashboard", "/search", "/company", "/person")
AUTH_PATHS = ("/login", "/sign-in", "/signin", "/auth", "/verify")
OTP_SIGNS = (
    "verification code", "enter the code", "check your email",
    "verify your identity", "one-time code", "security code",
)
CHALLENGE_SIGNS = (
    "just a moment", "verify you are human", "checking your browser",
    "captcha", "cloudflare",
)
BAD_CREDENTIAL_SIGNS = (
    "incorrect email", "incorrect password", "invalid credentials",
    "email or password is incorrect", "wrong password",
)


def _text(el):
    if el is None:
        return ""
    return clean_text(el.inner_text())


def extract_card(page, card):
    """Extract name/title/employer/email from one result card element."""
    name = _text(card.locator(SEL["card_name"]).first) or ""
    title = _text(card.locator(SEL["card_title"]).first) or ""
    if not title:
        # Fallback: last non-empty paragraph on the card is usually the
        # current title when the primary class combo is absent.
        for p in card.locator("p").all():
            t = _text(p)
            if t and t != name:
                title = t
                break
    emp = _text(card.locator(SEL["card_employer"]).first) or ""
    email = ""
    email_links = card.locator("a[data-testid='email-phone-text-desktop'], "
                               "a[data-testid='email-phone-text-mobile']")
    n = email_links.count()
    for i in range(n):
        v = _text(email_links.nth(i))
        if EMAIL_RE.fullmatch(v):
            email = v.lower()
            break
    return {
        "name": name,
        "current_title": title,
        "current_employer": emp,
        "email": email,
    }


def enrich_firm(page, firm, headful, delay, debug=False):
    result_rows = []
    try:
        candidates, note = find_candidates(page, firm, headful, delay, debug=debug)
    except RuntimeError as e:
        return [], "api_error", str(e)

    if not candidates:
        return [], "no_match", note

    picked = 0
    for cand in candidates:
        if picked >= MAX_CONTACTS_PER_FIRM:
            break
        prio = cand.get("prio")
        if prio not in PRIORITY_LABEL:
            continue
        first, last = split_name(clean_text(cand["name"]))
        if not first:
            continue
        title = clean_text(cand["current_title"])
        email = cand.get("email", "")
        row = {
            "vc_name": firm["vc_name"],
            "first_name": first,
            "last_name": last,
            "primary_email": email,
            "website": firm["website"],
            "normalized_firm_name": firm["normalized_firm_name"],
            "normalized_domain": firm["normalized_domain"],
            "contact_title": title,
            "contact_priority": PRIORITY_LABEL[prio],
            "source": "rocketreach_web",
            "confidence": "",
            "lookup_status": "complete" if email else "found_no_email",
            "notes": "",
        }
        if not email:
            revealed = reveal_email(page, cand["card"], headful)
            if revealed and EMAIL_RE.fullmatch(revealed):
                row["primary_email"] = revealed
                row["lookup_status"] = "complete"
            else:
                row["lookup_status"] = "found_no_email"
                row["notes"] = "no email shown on RocketReach card"

        conf = 0.4 + (0.2 if prio <= 2 else 0.1 if prio <= 4 else 0.0)
        core = firm["normalized_domain"].split(".")[0]
        emp_domain = cand.get("employer_domain", "")
        if core and core in (emp_domain or ""):
            conf += 0.1
        if row["primary_email"]:
            conf = min(conf + 0.3, 0.99)
        row["confidence"] = round(min(conf, 0.99), 2)
        result_rows.append(row)
        picked += 1
    return result_rows, "", ""


def _person_url_for_company(page, firm, headful, delay):
    """Resolve the firm to a RocketReach people-search URL.

    Goes to the company search filtered by domain (or name), clicks
    "Search Employees", and returns the resulting /person?employer[]=... URL.
    """
    target = firm["normalized_domain"] or firm["normalized_firm_name"]
    query = firm["normalized_domain"] or firm["vc_name"]
    page.goto(f"{COMPANY_URL}?domain={urllib.parse.quote(query)}",
              wait_until="domcontentloaded")
    for _ in range(8):
        time.sleep(delay + 2.0)
        if pg_title_ok(page):
            break
    btn = page.locator(SEL["company_search_employees"]).first
    try:
        btn.wait_for(state="visible", timeout=45000)
        btn.click(timeout=10000)
    except Exception:
        return None, "company not found on RocketReach"
    time.sleep(delay + 2.0)
    return page.url, ""


def pg_title_ok(page):
    title = (page.title() or "").lower()
    return "just a moment" not in title and title != ""


def _advance_page_start(url, step):
    """Return the same /person URL with its `start` param moved by `step`."""
    u = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    try:
        cur = 1
        for k, v in q:
            if k == "start":
                cur = int(v)
                break
    except (TypeError, ValueError):
        cur = 1
    new = []
    had = False
    for k, v in q:
        if k == "start":
            v = str(cur + step)
            had = True
        new.append((k, v))
    if not had:
        new.append(("start", str(cur + step)))
    return urllib.parse.urlunparse(
        u._replace(query=urllib.parse.urlencode(new))
    )


def _open_results(page, url, delay):
    page.goto(url, wait_until="domcontentloaded")
    for _ in range(8):
        time.sleep(delay + 2.0)
        if pg_title_ok(page):
            break
    try:
        page.locator(SEL["result_card"]).first.wait_for(
            state="visible", timeout=30000
        )
    except Exception:
        pass


def find_candidates(page, firm, headful, delay, debug=False):
    """Resolve the firm to a people-search URL and return ranked candidates.

    Title reads are free (only email reveals consume credits), so card
    scanning is widened well beyond the MAX_CONTACTS_PER_FIRM credit cap:
    up to MAX_SCAN_CARDS cards across result pages are examined. With
    `debug` enabled the per-card verdict (name/title/allowed/reason) is
    printed so a "no_match" firm is self-explanatory.
    """
    person_url, note = _person_url_for_company(page, firm, headful, delay)
    if not person_url:
        return [], note or "no people search URL for firm"

    cands = []
    scanned = 0
    offset = 1
    first_page = True
    while scanned < MAX_SCAN_CARDS:
        url = person_url if offset == 1 else _advance_page_start(person_url, offset - 1)
        _open_results(page, url, delay)
        cards = page.locator(SEL["result_card"])
        count = cards.count()
        if first_page and count == 0:
            return [], "no people found for firm on RocketReach"
        first_page = False

        for i in range(min(count, SCAN_PAGE_SIZE)):
            if scanned >= MAX_SCAN_CARDS:
                break
            card = cards.nth(i)
            data = extract_card(page, card)
            title = clean_text(data["current_title"])
            allowed = bool(title and title_allowed(title))
            reason = ""
            prio = None
            if allowed:
                prio = title_priority(title)
                if prio is None:
                    allowed = False
                    reason = "priority unmapped"
            elif not title:
                reason = "no title"
            else:
                reason = "role not matched"
            if debug:
                verdict = "OK" if allowed else "--"
                name = data["name"][:26] or "?"
                print(f"      [{scanned + 1:3d}] {verdict} {name:<26} "
                      f"{title[:46]:<46} {reason}")
            scanned += 1
            if not allowed:
                continue
            data["prio"] = prio
            data["card"] = card
            data["employer_domain"] = normalize_domain(
                data["current_employer"] or ""
            )
            cands.append(data)

        if count < SCAN_PAGE_SIZE or scanned >= MAX_SCAN_CARDS:
            break
        offset += SCAN_PAGE_SIZE

    if debug:
        print(f"      scanned {scanned} cards, {len(cands)} role-matching")
    cands.sort(key=lambda c: c["prio"])
    return cands, "no role-matching people on RocketReach"


def reveal_email(page, card, headful):
    """Click 'Get Contact Info' on a card and return the revealed email."""
    btn = None
    try:
        btn = card.locator(SEL["reveal_button"]).first
        if btn.count() == 0:
            btn = None
    except Exception:
        btn = None
    if btn is None:
        try:
            btn = page.locator(SEL["reveal_button"]).first
            if btn.count() == 0:
                btn = None
        except Exception:
            btn = None
    if btn is not None:
        try:
            btn.click(timeout=8000)
            time.sleep(2.0)
        except Exception:
            pass
    # After reveal, the card gets an email link with data-testid.
    try:
        email_links = card.locator("a[data-testid='email-phone-text-desktop'], "
                                   "a[data-testid='email-phone-text-mobile']")
        n = email_links.count()
        for i in range(n):
            v = _text(email_links.nth(i))
            if EMAIL_RE.fullmatch(v):
                return v.lower()
    except Exception:
        pass
    return ""


def _visible(locator):
    try:
        return locator.count() > 0 and locator.is_visible()
    except Exception:
        return False


def _page_summary(page):
    try:
        return clean_text(page.title()), page.url
    except Exception:
        return "", ""


def _body_text(page, limit=1200):
    try:
        return clean_text(page.locator("body").inner_text(timeout=3000))[:limit]
    except Exception:
        return ""


def _is_logged_in(page):
    """Recognise a valid session without depending on one fragile nav selector."""
    try:
        if _visible(page.locator(SEL["logged_in_marker"]).first):
            return True
    except Exception:
        pass

    title, url = _page_summary(page)
    if any(sign in title.lower() for sign in CHALLENGE_SIGNS):
        return False
    try:
        if _visible(page.locator(SEL["login_password"]).first):
            return False
    except Exception:
        pass
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "/").lower().rstrip("/") or "/"
    except Exception:
        return False
    if host != "rocketreach.co" and not host.endswith(".rocketreach.co"):
        return False
    if any(path == p or path.startswith(f"{p}/") for p in AUTH_PATHS):
        return False
    return any(path == p or path.startswith(f"{p}/")
               for p in AUTHENTICATED_PATHS)


def _login_blocker(page):
    """Return a useful diagnosis; never attempts to solve a challenge."""
    title, _ = _page_summary(page)
    body = _body_text(page).lower()
    combined = f"{title.lower()} {body}"
    if any(sign in combined for sign in BAD_CREDENTIAL_SIGNS):
        return "RocketReach rejected the email or password"
    if any(sign in combined for sign in OTP_SIGNS):
        return "RocketReach is waiting for an emailed verification code"
    if any(sign in combined for sign in CHALLENGE_SIGNS):
        return "a Cloudflare/CAPTCHA challenge is still open"
    if _visible(page.locator(SEL["login_password"]).first):
        return "the login form is still visible"
    return "RocketReach did not expose a recognised signed-in page"


def _failure_message(page, prefix):
    title, url = _page_summary(page)
    return (f"{prefix}: {_login_blocker(page)}. Final page: "
            f"{url or '?'} (title: {title or '?'}).")


def _saved_session_is_valid(page, timeout=20):
    try:
        page.goto(PERSON_URL, wait_until="domcontentloaded")
    except Exception:
        return False
    deadline = time.time() + min(max(timeout, 1), 20)
    while time.time() < deadline:
        if _is_logged_in(page):
            return True
        _, url = _page_summary(page)
        try:
            path = urllib.parse.urlparse(url).path.lower()
        except Exception:
            path = ""
        if any(path == p or path.startswith(f"{p}/") for p in AUTH_PATHS):
            return False
        time.sleep(0.5)
    return False


def _save_session(context, path):
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    context.storage_state(path=path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def login(page, email, password, headful, timeout=DEFAULT_LOGIN_TIMEOUT):
    if not email or not password:
        raise RuntimeError("set ROCKETREACH_EMAIL/ROCKETREACH_PASSWORD "
                           "or pass --email/--password")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    if _is_logged_in(page):
        return
    # Wait out any Cloudflare "Just a moment" challenge before the form shows.
    em = page.locator(SEL["login_email"]).first
    deadline = time.time() + timeout
    form_ready = False
    last_report = 0.0
    while time.time() < deadline:
        if _is_logged_in(page):
            return
        if _visible(em):
            form_ready = True
            break
        now = time.time()
        if headful and now - last_report >= 15:
            last_report = now
            print(f"    waiting for login form — {_login_blocker(page)}…")
        time.sleep(1.0)
    if not form_ready:
        raise RuntimeError(_failure_message(
            page, "login form did not become available"
        ))
    em.fill(email)
    pw = page.locator(SEL["login_password"]).first
    pw.fill(password)
    try:
        page.locator(SEL["login_submit"]).first.click(timeout=8000)
    except Exception:
        pw.press("Enter")
    if headful:
        print("  login submitted — complete any CAPTCHA or emailed code "
              "manually in the browser; the script will continue "
              "automatically after verification…")
    deadline = time.time() + timeout
    last_report = 0.0
    while time.time() < deadline:
        if _is_logged_in(page):
            return
        blocker = _login_blocker(page)
        if blocker == "RocketReach rejected the email or password":
            raise RuntimeError(_failure_message(page, "login failed"))
        now = time.time()
        if headful and now - last_report >= 15:
            last_report = now
            print(f"    still waiting — {blocker}…")
        time.sleep(1.0)
    # The site may redirect to a newly named landing route after login. Probe a
    # known authenticated route once before declaring failure.
    if _saved_session_is_valid(page, timeout=min(timeout, 20)):
        return
    raise RuntimeError(_failure_message(page, "login timed out"))


def dump_selectors(email, password, headful,
                   timeout=DEFAULT_LOGIN_TIMEOUT):
    """Navigate the live site and print its structure to discover selectors."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            login(page, email, password, headful, timeout=timeout)
        except RuntimeError as e:
            print("login error:", e)
            if headful:
                input("press Enter in terminal once logged in…")
        print("\n=== People search page structure ===")
        page.goto(PERSON_URL, wait_until="domcontentloaded")
        for _ in range(10):
            time.sleep(2)
            if pg_title_ok(page):
                break
        dump = page.evaluate(
            """() => {
                const pick = (el, label) => {
                    if (!el) return;
                    console.log(label, '->', el.tagName,
                                'class='+JSON.stringify(el.className),
                                'id='+el.id,
                                'name='+(el.name||''));
                };
                const inputs = [...document.querySelectorAll('input')];
                inputs.slice(0,12).forEach(i =>
                    pick(i, 'input['+i.type+'] placeholder='+JSON.stringify(i.placeholder)));
                const btns = [...document.querySelectorAll('button')].slice(0,20);
                btns.forEach(b => pick(b, 'button '+JSON.stringify(b.innerText.slice(0,30))));
            }"""
        )
        # Fallback structure dump of candidate containers
        info = page.evaluate(
            """() => {
                const out = {};
                out.url = location.href;
                out.forms = [...document.querySelectorAll('form')].map(f=>f.outerHTML.slice(0,300));
                out.sample = document.body.innerText.slice(0,1000);
                return out;
            }"""
        )
        print("URL:", info["url"])
        print("BODY:\n", info["sample"])
        browser.close()


def run(args):
    email = args.email or os.environ.get("ROCKETREACH_EMAIL", "").strip()
    password = args.password or os.environ.get("ROCKETREACH_PASSWORD", "").strip()

    try:
        df = pd.read_excel(args.input)
    except FileNotFoundError:
        sys.exit(f"input file not found: {args.input}")
    from auto_enrich import REQUIRED
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
    print(f"{len(firms)} unique firms")

    if args.plan:
        print("\nPLAN ONLY — no browser, no login. Firms:")
        for i, f in enumerate(firms, 1):
            tgt = f["normalized_domain"] or f["normalized_firm_name"]
            print(f"  {i:3d}. {f['vc_name']} -> search {tgt}")
        print("run again without --plan to execute")
        return

    all_rows = []
    no_matches = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        session_file = str(getattr(args, "session_file", "") or "").strip()
        fresh_login = bool(getattr(args, "fresh_login", False))
        use_saved = bool(session_file and os.path.isfile(session_file)
                         and not fresh_login)
        context_options = {"storage_state": session_file} if use_saved else {}
        try:
            ctx = browser.new_context(**context_options)
        except Exception as exc:
            if not use_saved:
                raise
            print(f"saved session could not be loaded ({exc}); logging in again")
            use_saved = False
            ctx = browser.new_context()
        page = ctx.new_page()
        try:
            authenticated = False
            if use_saved:
                print(f"checking saved RocketReach session: {session_file}")
                authenticated = _saved_session_is_valid(page, args.timeout)
                if authenticated:
                    print("using saved RocketReach session")
                else:
                    print("saved RocketReach session expired; logging in again")
            if not authenticated:
                print("logging in…")
                login(page, email, password, args.headful,
                      timeout=args.timeout)
                print("logged in")
            if session_file:
                _save_session(ctx, session_file)
                print(f"saved RocketReach session: {session_file}")

            for i, firm in enumerate(firms, 1):
                label = firm["vc_name"] or firm["normalized_domain"]
                print(f"[{i}/{len(firms)}] {label}")
                rows, status, note = enrich_firm(
                    page, firm, args.headful, args.delay, debug=args.debug
                )
                if status == "api_error":
                    print(f"    ERROR: {note}")
                    continue
                if status == "no_match":
                    no_matches.append({
                        "vc_name": firm["vc_name"], "website": firm["website"],
                        "normalized_domain": firm["normalized_domain"],
                        "lookup_status": "no_match", "notes": note,
                    })
                    print(f"    no_match ({note})")
                    continue
                for r in rows:
                    print(f"    {r['contact_priority']:<18} "
                          f"{r['first_name']} {r['last_name']} "
                          f"<{r['primary_email'] or '-'}>")
                all_rows.extend(rows)
        finally:
            ctx.close()
            browser.close()

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


def main():
    ap = argparse.ArgumentParser(
        description="Enrich a VC workbook by scraping RocketReach in a browser."
    )
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="final_workbook.xlsx")
    ap.add_argument("--email", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between requests")
    ap.add_argument("--headful", action="store_true",
                    help="show the browser (needed to complete 2FA/CAPTCHA)")
    ap.add_argument("--plan", action="store_true",
                    help="print the plan and exit (no login/browser)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_LOGIN_TIMEOUT,
                    help="seconds to wait for login/2FA/CAPTCHA to complete "
                         f"(default {DEFAULT_LOGIN_TIMEOUT})")
    ap.add_argument("--session-file", default=".rocketreach-auth.json",
                    help="reuse signed-in browser state (contains sensitive "
                         "cookies; default .rocketreach-auth.json)")
    ap.add_argument("--fresh-login", action="store_true",
                    help="ignore any saved session and perform a fresh login")
    ap.add_argument("--debug", action="store_true",
                    help="print per-card scan verdicts while searching firms")
    ap.add_argument("--dump-selectors", action="store_true",
                    help="login and print live page structure to calibrate "
                         "the SEL config, then exit")
    args = ap.parse_args()

    email = args.email or os.environ.get("ROCKETREACH_EMAIL", "").strip()
    password = args.password or os.environ.get("ROCKETREACH_PASSWORD", "").strip()
    if args.dump_selectors:
        dump_selectors(email, password, args.headful, timeout=args.timeout)
        return
    usable_session = (args.session_file and os.path.isfile(args.session_file)
                      and not args.fresh_login)
    if (not email or not password) and not usable_session:
        sys.exit("set ROCKETREACH_EMAIL/ROCKETREACH_PASSWORD (or --email/--password)")
    run(args)


if __name__ == "__main__":
    main()
