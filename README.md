# VC Workbook Enrichment

Three independent pipelines, all fully automated:

1. **`auto_enrich.py`** — official RocketReach API (needs `ROCKETREACH_API_KEY`)
2. **`website_enrich.py`** — crawls each VC's own website team pages (no API,
   no account, no credits)
3. **`rocketreach_web.py`** — drives RocketReach in a real browser via
   Playwright, logging in with your account and scraping the People Search UI
   (no API key)

Both take the same input workbook (`vc_name`, `website` filled) and produce
the same output format: up to 3 role-verified investor contacts per firm,
best-ranked first, with full helper columns.

All three enforce the same behavioral rules (below).

## Quick start

    python3 -m venv .venv && .venv/bin/pip install -q pandas openpyxl lxml

### Option 1: RocketReach API

    export ROCKETREACH_API_KEY="your_key"
    .venv/bin/python auto_enrich.py --input <file>.xlsx          # dry-run plan
    .venv/bin/python auto_enrich.py --input <file>.xlsx --go     # spends credits

### Option 2: VC websites only (no API)

    .venv/bin/python website_enrich.py --input <file>.xlsx

Crawls each firm's domain politely (≤8 pages, same-domain links matching
team/about/people hints, configurable delay). Extracts names + titles from
team pages, captures mailto links and de-obfuscates `name [at] domain`
patterns, filters to investor roles only, ranks by seniority.

### Option 3: RocketReach via browser (Playwright)

    .venv/bin/pip install playwright && .venv/bin/playwright install chromium
    export ROCKETREACH_EMAIL="you@example.com"
    read -s "ROCKETREACH_PASSWORD?RocketReach password: "; export ROCKETREACH_PASSWORD; echo
    .venv/bin/python rocketreach_web.py --input <file>.xlsx            # real run
    .venv/bin/python rocketreach_web.py --input <file>.xlsx --headful  # watch + finish 2FA
    .venv/bin/python rocketreach_web.py --input <file>.xlsx --headful --timeout 300
    .venv/bin/python rocketreach_web.py --input <file>.xlsx --plan     # plan only, no login

Logs into RocketReach, resolves each firm via the company search (domain,
else firm name), follows "Search Employees" to the people-search results,
applies the investor-title filter, then clicks "Get Contact Info" on the top
role-ranked candidates to reveal and capture emails. `--headful` keeps the
browser visible — **recommended, often required** because RocketReach sits
behind a Cloudflare challenge that blocks headless/automated browsers and may
occasionally ask for manual verification.

> **Calibrating selectors after a RocketReach redesign:** RocketReach's page
> markup is not a public, stable API. If the scraper stops finding data, run
>     .venv/bin/python rocketreach_web.py --input <file>.xlsx --dump-selectors --headful
> to print the live page structure, then update the `SEL` dict at the top of
> `rocketreach_web.py`.

> **Note:** this skips the API and scrapes the web UI. It still uses your
> account's lookup credits when you reveal emails ("Get Contact Info"), and
> automation of the site may violate RocketReach's terms of service. Run
> responsibly and at modest delay (default 1s between requests).

After a successful login, the browser authentication state is saved to
`.rocketreach-auth.json` and reused on later runs. This avoids presenting
RocketReach with a completely new session every time. The file contains
sensitive session cookies, is excluded by `.gitignore`, and is created with
owner-only permissions. Use `--fresh-login` to ignore it when it expires or
when you intentionally change accounts. CAPTCHA and emailed-code verification
remain manual in `--headful` mode; the script only waits for completion. The
default login timeout is 15 seconds; use `--timeout 300` when manual
verification needs longer.

## What all pipelines enforce

- Roles kept: managing/general/founding partner, partner, venture partner,
  principal, investment manager, investment team, senior/investment associate
- Roles rejected: founders-only profiles, analysts, interns, operations,
  finance, legal, marketing, recruiting, engineering
- Generic inboxes (info@, contact@, …) never attributed to individuals
- Max 3 contacts per firm; one contact per row; near-duplicate firms merged
- Output: sheet `contacts` + sheet `no_match_firms`

Columns written: `first_name`, `last_name`, `primary_email`,
`normalized_firm_name`, `normalized_domain`, `contact_title`,
`contact_priority`, `source`, `confidence`, `lookup_status`, `notes`.

Statuses: `complete` (email verified/present), `found_no_email` (strong
contact, no email available — kept for manual follow-up), `no_match`.

## Tests (mocked, no network, no credits)

    .venv/bin/python mock_test.py            # API pipeline
    .venv/bin/python mock_test_website.py    # website pipeline
    .venv/bin/python mock_test_rrweb.py      # RocketReach web pipeline

## Notes

- RR endpoint paths are constants at the top of `auto_enrich.py`.
- Website crawling respects same-domain boundaries and rate-limits itself;
  review robots.txt of target sites for compliance-sensitive use.
