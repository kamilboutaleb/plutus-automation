# VC Workbook Enrichment

Two independent pipelines, both fully automated:

1. **`auto_enrich.py`** — official RocketReach API (needs `ROCKETREACH_API_KEY`)
2. **`website_enrich.py`** — crawls each VC's own website team pages (no API,
   no account, no credits)

Both take the same input workbook (`vc_name`, `website` filled) and produce
the same output format: up to 3 role-verified investor contacts per firm,
best-ranked first, with full helper columns.

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

## What both pipelines enforce

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

## Notes

- RR endpoint paths are constants at the top of `auto_enrich.py`.
- Website crawling respects same-domain boundaries and rate-limits itself;
  review robots.txt of target sites for compliance-sensitive use.

