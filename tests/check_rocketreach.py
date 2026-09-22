import atexit
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scratch = tempfile.TemporaryDirectory(prefix="vc-rocketreach-check-")
atexit.register(scratch.cleanup)
os.chdir(scratch.name)

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


def fake_reveal_candidate(page, candidate, headful, delay, reveal_timeout):
    return REVEALS.get(candidate.get("name", ""), ""), ""


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


real_reveal_candidate_email = rw.reveal_candidate_email
real_reveal_email = rw.reveal_email
rw.find_candidates = fake_find
rw.reveal_candidate_email = fake_reveal_candidate
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
    resume = False
    retry_no_match = False
    retry_missing_emails = False
    reveal_timeout = 15.0


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

# A Playwright locator that disappears during a RocketReach re-render must not
# abort the whole batch.
class TimedOutLocator:
    def inner_text(self, **_kwargs):
        raise TimeoutError("stale card")


assert rw._text(TimedOutLocator()) == ""

# Checkpoints preserve both successful and no-match firms and can be loaded by
# a later --resume run without changing the contacts-sheet contract.
checkpoint = "test_rr_checkpoint.xlsx"
checkpoint_rows = out.to_dict("records")
checkpoint_no_matches = nm.fillna("").to_dict("records")
rw._write_checkpoint(checkpoint, checkpoint_rows, checkpoint_no_matches, [])
loaded_rows, loaded_no_matches, loaded_errors = rw._load_checkpoint(checkpoint)
assert len(loaded_rows) == len(checkpoint_rows)
assert len(loaded_no_matches) == len(checkpoint_no_matches)
assert loaded_errors == []
assert list(pd.read_excel(checkpoint, sheet_name="contacts").columns) == rw.FINAL_COLS
assert list(pd.read_excel(checkpoint, sheet_name="no_match_firms").columns) == [
    "vc_name", "website", "normalized_domain", "lookup_status", "notes",
]

# Resume keys use normalized domain first and normalized firm name as a stable
# fallback, so name-only inputs can also be skipped safely.
assert rw._firm_key({"normalized_domain": "acme.vc"}) == "domain:acme.vc"
assert rw._firm_key({"vc_name": "Beta Capital", "normalized_domain": ""}) == \
    "name:beta"

# A refreshed card must still belong to the expected person before a reveal
# can consume a lookup credit or attach an email.
reveal_calls = []
rw._open_results = lambda page, url, delay: None
rw._find_card_by_id = lambda page, profile_card_id: object()
rw.extract_card = lambda page, card: {"name": "Different Person", "email": ""}
rw.reveal_email = lambda *args, **kwargs: reveal_calls.append(True) or "wrong@example.com"
email, note = real_reveal_candidate_email(
    object(),
    {"profile_card_id": "42", "result_url": "https://example.test/results",
     "name": "Expected Person"},
    False, 0, 15.0,
)
assert email == ""
assert "identity changed" in note
assert reveal_calls == []

rw.extract_card = lambda page, card: {"name": "Expected Person", "email": ""}
rw.reveal_email = lambda *args, **kwargs: "expected@example.com"
email, note = real_reveal_candidate_email(
    object(),
    {"profile_card_id": "42", "result_url": "https://example.test/results",
     "name": "Expected Person"},
    False, 0, 15.0,
)
assert email == "expected@example.com"
assert note == ""

# One reveal click can complete asynchronously. Poll the verified card until a
# delayed email appears, without clicking again and consuming another lookup.
class DelayedEmailLinks:
    def __init__(self, state):
        self.state = state

    def count(self):
        self.state["polls"] += 1
        return int(self.state["polls"] >= 3)

    def nth(self, _index):
        return self

    def inner_text(self, **_kwargs):
        return "delayed@example.com"


class RevealButton:
    @property
    def first(self):
        return self

    def count(self):
        return 1

    def click(self, **_kwargs):
        delayed_state["clicks"] += 1


class DelayedRevealCard:
    def locator(self, selector):
        if selector == rw.SEL["reveal_button"]:
            return RevealButton()
        return DelayedEmailLinks(delayed_state)


delayed_state = {"clicks": 0, "polls": 0}
assert real_reveal_email(
    object(), DelayedRevealCard(), False,
    reveal_timeout=0.2, poll_interval=0.01,
) == "delayed@example.com"
assert delayed_state["clicks"] == 1
assert delayed_state["polls"] >= 3

# A per-firm browser timeout is checkpointed as retryable while later firms
# continue. A resume run skips completed firms and retries only that failure.
def flaky_find(page, firm, headful, delay, debug=False):
    if firm["vc_name"].startswith("Beta"):
        raise TimeoutError("card re-rendered")
    return fake_find(page, firm, headful, delay, debug=debug)


class ResumeArgs(FakeArgs):
    output = "test_rr_resume.xlsx"


rw.find_candidates = flaky_find
rw.reveal_candidate_email = fake_reveal_candidate
rw.run(args=ResumeArgs())
partial_rows, partial_no_matches, partial_errors = rw._load_checkpoint(
    ResumeArgs.output
)
assert {row["vc_name"] for row in partial_rows} == {"Acme Ventures"}
assert {row["vc_name"] for row in partial_no_matches} == {"Gamma Ventures"}
assert {row["vc_name"] for row in partial_errors} == {"Beta Capital"}

resumed_searches = []
def resumed_find(page, firm, headful, delay, debug=False):
    resumed_searches.append(firm["vc_name"])
    return fake_find(page, firm, headful, delay, debug=debug)


ResumeArgs.resume = True
rw.find_candidates = resumed_find
rw.run(args=ResumeArgs())
resumed_rows, resumed_no_matches, resumed_errors = rw._load_checkpoint(
    ResumeArgs.output
)
assert resumed_searches == ["Beta Capital"]
assert {row["vc_name"] for row in resumed_rows} == {
    "Acme Ventures", "Beta Capital",
}
assert {row["vc_name"] for row in resumed_no_matches} == {"Gamma Ventures"}
assert resumed_errors == []

# A targeted resume can revisit only firms that previously had one or more
# contacts without an email, while retaining fully completed firms/no-matches.
missing_email_searches = []
def missing_email_find(page, firm, headful, delay, debug=False):
    missing_email_searches.append(firm["vc_name"])
    return fake_find(page, firm, headful, delay, debug=debug)


class MissingEmailArgs(FakeArgs):
    output = "test_rr_out.xlsx"
    resume = True
    retry_missing_emails = True


rw.find_candidates = missing_email_find
rw.run(args=MissingEmailArgs())
assert missing_email_searches == ["Acme Ventures"]

# RocketReach's account-wide daily search quota is fatal for the batch. The
# current firm remains retryable, its checkpoint is saved, and later firms are
# not searched or incorrectly recorded as no-match.
limit_page = StatePage(
    "https://rocketreach.co/person",
    body="You've reached your daily search limit",
)
try:
    rw._raise_if_daily_search_limit(limit_page)
except rw.DailySearchLimitError:
    pass
else:
    raise AssertionError("daily search limit page was not detected")

limited_searches = []
def limited_find(page, firm, headful, delay, debug=False):
    limited_searches.append(firm["vc_name"])
    if firm["vc_name"].startswith("Beta"):
        raise rw.DailySearchLimitError(
            "RocketReach daily search limit reached"
        )
    return fake_find(page, firm, headful, delay, debug=debug)


class LimitArgs(FakeArgs):
    output = "test_rr_limit.xlsx"


rw.find_candidates = limited_find
rw.run(args=LimitArgs())
limit_rows, limit_no_matches, limit_errors = rw._load_checkpoint(
    LimitArgs.output
)
assert limited_searches == ["Acme Ventures", "Beta Capital"]
assert {row["vc_name"] for row in limit_rows} == {"Acme Ventures"}
assert limit_no_matches == []
assert {row["vc_name"] for row in limit_errors} == {"Beta Capital"}
assert limit_errors[0]["lookup_status"] == "daily_search_limit"
print("ALL MOCK TESTS PASSED (rr web)")
