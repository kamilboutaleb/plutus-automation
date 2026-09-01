import os
import sys

import pandas as pd

import rocketreach_web as rw

rows = [
    {"vc_name": "Acme Ventures", "website": "https://www.acme.vc/team"},
    {"vc_name": "Acme Venture Capital", "website": "acme.vc"},
    {"vc_name": "Beta Capital", "website": ""},
    {"vc_name": "Gamma Ventures", "website": "https://gamma.vc"},
]
pd.DataFrame(rows, columns=["vc_name", "first_name", "last_name",
                            "primary_email", "website"]).to_excel(
    "test_rr_input.xlsx", index=False)

CANDIDATES = {
    "acme.vc": [
        {"name": "Jane Smith", "current_title": "Managing Partner",
         "current_employer": "acme.vc", "prio": 1, "email": "jane@acme.vc",
         "employer_domain": "acme.vc", "card": None},
        {"name": "John Roe", "current_title": "Partner, Investments",
         "current_employer": "acme.vc", "prio": 2, "email": "",
         "employer_domain": "acme.vc", "card": None},
    ],
    "beta capital": [
        {"name": "Alice Brown", "current_title": "General Partner",
         "current_employer": "betacap.com", "prio": 1, "email": "",
         "employer_domain": "", "card": {"name": "Alice Brown"}},
        {"name": "Carl Doe", "current_title": "Senior Associate",
         "current_employer": "betacap.com", "prio": 6, "email": "",
         "employer_domain": "", "card": {"name": "Carl Doe"}},
    ],
    "gamma ventures": [],
}

REVEALS = {
    "Alice Brown": "alice@betacap.com",
    "Carl Doe": "carl@betacap.com",
}


def fake_find(page, firm, headful, delay):
    if firm["normalized_domain"] == "acme.vc":
        key = "acme.vc"
    elif firm["vc_name"].startswith("Beta"):
        key = "beta capital"
    else:
        key = "gamma ventures"
    return CANDIDATES.get(key, []), "no match"


def fake_reveal(page, card, headful):
    if card is None:
        return ""
    return REVEALS.get(card.get("name", ""), "")


class FakeChromium:
    def launch(self, *a, **k):
        return self

    def new_context(self, *a, **k):
        return FakeContext()

    def close(self, *a, **k):
        return None


class FakeContext:
    def new_page(self, *a, **k):
        return object()


class FakeSyncPlaywright:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def chromium(self):
        return FakeChromium()


rw.find_candidates = fake_find
rw.reveal_email = fake_reveal
rw.sync_playwright = lambda: FakeSyncPlaywright()


def fake_login(page, email, password, headful, timeout=120):
    assert email and password
    print("fake login ok")


rw.login = fake_login


class FakeArgs:
    input = "test_rr_input.xlsx"
    output = "test_rr_out.xlsx"
    email = "test@example.com"
    password = "test"
    delay = 0.0
    headful = False
    plan = False


rw.run(args=FakeArgs())

out = pd.read_excel("test_rr_out.xlsx", sheet_name="contacts")
nm = pd.read_excel("test_rr_out.xlsx", sheet_name="no_match_firms")
print(out[["vc_name", "first_name", "last_name", "primary_email",
           "contact_priority", "lookup_status"]].to_string())
print(nm.to_string())

assert len(out) == 4, f"expected 4 rows, got {len(out)}"
# Jane has email on card; Alice/Carl get emails via reveal; John not.
assert (out["primary_email"].notna()).sum() == 3
assert set(nm["vc_name"]) == {"Gamma Ventures"}
assert out[out["last_name"] == "Smith"]["lookup_status"].iloc[0] == "complete"
assert out[out["last_name"] == "Roe"]["lookup_status"].iloc[0] == "found_no_email"
assert out[out["last_name"] == "Brown"]["lookup_status"].iloc[0] == "complete"
assert out[out["last_name"] == "Doe"]["lookup_status"].iloc[0] == "complete"
assert len(out[out["vc_name"] == "Acme Ventures"]) <= 3
assert not (out["last_name"].isin(["Analyst", "Ceo"])).any()
assert (out["source"] == "rocketreach_web").all()
assert out["confidence"].notna().all()
print("ALL MOCK TESTS PASSED (rr web)")
