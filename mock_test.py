import os
import sys

import pandas as pd

import auto_enrich as ae

rows = [
    {"vc_name": "Acme Ventures", "website": "https://www.acme.vc/team"},
    {"vc_name": "Acme Venture Capital", "website": "acme.vc"},
    {"vc_name": "Beta Capital", "website": ""},
    {"vc_name": "Gamma Ventures", "website": "https://gamma.vc"},
]
pd.DataFrame(rows, columns=["vc_name", "first_name", "last_name",
                            "primary_email", "website"]).to_excel(
    "test_input.xlsx", index=False)

SEARCHES = {
    "acme.vc": [
        {"id": 101, "name": "Jane Smith",
         "current_title": "Managing Partner",
         "current_employer": "Acme Ventures", "current_employer_domain": "acme.vc"},
        {"id": 102, "name": "Bob Analyst",
         "current_title": "Analyst",
         "current_employer": "Acme Ventures", "current_employer_domain": "acme.vc"},
        {"id": 103, "name": "Sarah Ceo",
         "current_title": "Founder & CEO",
         "current_employer": "StartupCo", "current_employer_domain": "startup.co"},
        {"id": 104, "name": "John Roe",
         "current_title": "Partner, Investments",
         "current_employer": "Acme Ventures", "current_employer_domain": "acme.vc"},
    ],
    "beta capital": [
        {"id": 201, "name": "Alice Brown",
         "current_title": "General Partner",
         "current_employer": "Beta Capital"},
        {"id": 202, "name": "Carl Doe",
         "current_title": "Senior Associate",
         "current_employer": "Beta Capital"},
    ],
    "gamma ventures": [],
}

LOOKUPS = {
    101: {"id": 101, "name": "Jane Smith", "email": "jane@acme.vc",
          "smtp_valid": "valid", "current_title": "Managing Partner"},
    104: {"id": 104, "name": "John Roe", "email": None,
          "smtp_valid": None, "current_title": "Partner, Investments"},
    201: {"id": 201, "name": "Alice Brown", "email": "alice@betacp.com",
          "smtp_valid": "valid", "current_title": "General Partner"},
    202: {"id": 202, "name": "Carl Doe", "email": "carl@betacp.com",
          "smtp_valid": "invalid", "current_title": "Senior Associate"},
}


class MockClient:
    delay = 0
    lookups_used = 0
    searches_used = 0

    def call(self, method, endpoint, params=None, body=None):
        if method == "POST":
            q = body["query"]
            key = (q.get("current_employer_domain")
                   or q.get("current_employer"))[0]
            return {"people": SEARCHES.get(key.lower()
                    if not key.startswith("beta") else "beta capital", [])}
        pid = int(params["id"])
        return LOOKUPS[pid]


ae.ApiClient = lambda api_key, delay=1.0: MockClient()

os.environ["ROCKETREACH_API_KEY"] = "test"
sys.argv = ["auto_enrich.py", "--input", "test_input.xlsx",
            "--output", "test_out.xlsx", "--go"]
ae.main()

out = pd.read_excel("test_out.xlsx", sheet_name="contacts")
nm = pd.read_excel("test_out.xlsx", sheet_name="no_match_firms")
print(out[["vc_name", "first_name", "last_name", "primary_email",
           "contact_priority", "lookup_status"]].to_string())
print(nm.to_string())

expect_acme_emails = 1
assert len(out) == 4, f"expected 4 rows, got {len(out)}"
assert (out["primary_email"].notna()).sum() == expect_acme_emails + 1
assert set(nm["vc_name"]) == {"Gamma Ventures"}
assert out[out["last_name"] == "Smith"]["lookup_status"].iloc[0] == "complete"
assert out[out["last_name"] == "Roe"]["lookup_status"].iloc[0] == "found_no_email"
assert out[out["last_name"] == "Doe"]["lookup_status"].iloc[0] == "found_no_email"
assert len(out[out["vc_name"] == "Acme Ventures"]) <= 3
print("ALL MOCK TESTS PASSED")
