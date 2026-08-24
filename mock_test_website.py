import pandas as pd

import website_enrich as we

SITES = {
    "https://acme.vc": """
        <html><body>
        <a href="/team">Our Team</a>
        <footer>info@acme.vc</footer>
        </body></html>""",
    "https://acme.vc/team": """
        <html><body>
        <div class="person"><h3>Jane Smith</h3><p>Managing Partner</p>
            <a href="mailto:jane@acme.vc">Email</a></div>
        <div class="person"><h3>John Roe</h3><p>Partner, Investments</p></div>
        <div class="person"><h3>Sarah Wells</h3><p>Principal</p>
            sarah [at] acme [dot] vc</div>
        <div class="person"><h3>Bob Analyst</h3><p>Analyst</p></div>
        <div class="person"><h3>Dana Ops</h3><p>Head of Operations</p></div>
        <div class="person"><h3>Evan Founder</h3><p>Founder &amp; CEO</p></div>
        <div class="person"><h3>Tom Brown</h3><p>Associate</p></div>
        <footer>info@acme.vc | contact@acme.vc</footer>
        </body></html>""",
    "https://beta.vc": "",
    "https://gamma.vc": """
        <html><body><p>We invest in founders.</p></body></html>""",
}

visited = []


def fake_fetch(url, timeout=15):
    visited.append(url)
    return SITES.get(url, "")


we.fetch = fake_fetch

pd.DataFrame([
    {"vc_name": "Acme Ventures", "website": "https://www.acme.vc"},
    {"vc_name": "Beta Capital", "website": "beta.vc"},
    {"vc_name": "Gamma Ventures", "website": "gamma.vc"},
], columns=["vc_name", "first_name", "last_name",
            "primary_email", "website"]).to_excel("wt.xlsx", index=False)

import sys
sys.argv = ["website_enrich.py", "--input", "wt.xlsx",
            "--output", "wt_out.xlsx", "--delay", "0"]
we.main()

out = pd.read_excel("wt_out.xlsx", sheet_name="contacts")
nm = pd.read_excel("wt_out.xlsx", sheet_name="no_match_firms")
print(out[["vc_name", "first_name", "last_name", "primary_email",
           "contact_priority", "lookup_status"]].to_string())
print(nm[["vc_name", "lookup_status", "notes"]].to_string())

acme = out[out["vc_name"] == "Acme Ventures"]
assert len(acme) == 3, f"cap failed: {len(acme)}"
jane = acme[acme["last_name"] == "Smith"].iloc[0]
assert jane["primary_email"] == "jane@acme.vc"
assert not any("info@" in str(e) or "contact@" in str(e)
               for e in acme["primary_email"]), "generic email leaked"
sarah = acme[acme["last_name"] == "Wells"].iloc[0]
assert sarah["primary_email"] == "sarah@acme.vc", \
    f"obfuscation decode failed: {sarah['primary_email']}"
assert acme.iloc[0]["last_name"] == "Smith", "ranking failed"
assert set(nm["lookup_status"]) == {"no_match"}
bnote = nm[nm["vc_name"] == "Beta Capital"]["notes"].iloc[0]
assert bnote == "site unreachable"
print("ALL WEBSITE-CRAWLER TESTS PASSED")
