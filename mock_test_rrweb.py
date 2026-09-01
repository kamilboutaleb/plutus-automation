import os
import sys

import pandas as pd

import rocketreach_web as rw


class StateLocator:
    def __init__(self, visible=False, text=""):
        self._visible = visible
        self._text = text

    @property
    def first(self):
        return self

    def count(self):
        return int(self._visible)

    def is_visible(self):
        return self._visible

    def inner_text(self, **_kwargs):
        return self._text


class StatePage:
    def __init__(self, url, title="RocketReach", body="", password=False,
                 marker=False):
        self.url = url
        self._title = title
        self._body = body
        self._password = password
        self._marker = marker

    def title(self):
        return self._title

    def locator(self, selector):
        if selector == "body":
            return StateLocator(True, self._body)
        if selector == rw.SEL["logged_in_marker"]:
            return StateLocator(self._marker)
        if selector == rw.SEL["login_password"]:
            return StateLocator(self._password)
        return StateLocator()


# Login state checks are intentionally independent of RocketReach's nav DOM.
assert rw._is_logged_in(StatePage("https://rocketreach.co/person"))
assert rw._is_logged_in(StatePage("https://rocketreach.co/dashboard/new"))
assert not rw._is_logged_in(StatePage("https://rocketreach.co/login",
                                      password=True))
assert "verification code" in rw._login_blocker(StatePage(
    "https://rocketreach.co/verify", body="Enter the verification code"
))
assert "Cloudflare" in rw._login_blocker(StatePage(
    "https://rocketreach.co/login", title="Just a moment"
))
assert "rejected" in rw._login_blocker(StatePage(
    "https://rocketreach.co/login", body="Incorrect email or password"
))

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


def fake_find(page, firm, headful, delay, debug=False):
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

    def close(self, *a, **k):
        return None


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


def fake_login(page, email, password, headful,
               timeout=rw.DEFAULT_LOGIN_TIMEOUT):
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
    debug = False
    timeout = rw.DEFAULT_LOGIN_TIMEOUT
    session_file = ""
    fresh_login = False


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
