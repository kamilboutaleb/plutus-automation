import re

ALLOWED_TITLE_RE = re.compile(
    r"\b(partner|principal|associate|investment manager|investment team|"
    r"venture partner|managing partner|general partner|founding partner)\b",
    re.I,
)

EXCLUDE_TITLE_WORDS = [
    "analyst", "intern", "operations", "finance", "financial", "legal",
    "counsel", "compliance", "marketing", "talent", "platform", "recruiter",
    "recruiting", "chief of staff", "assistant", "office manager",
    "controller", "accounting", "communications", "founder", "co-founder",
    "ceo", "cto", "engineer", "developer", "designer",
]

PRIORITY_PATTERNS = [
    (r"\bmanaging partner\b|\bgeneral partner\b|\bfounding partner\b", 1),
    (r"\bpartner\b|\bventure partner\b", 2),
    (r"\bprincipal\b", 3),
    (r"\binvestment manager\b", 4),
    (r"\binvestment team\b", 5),
    (r"\bsenior associate\b|\binvestment associate\b|\bassociate\b", 6),
]

PRIORITY_LABEL = {
    1: "partner_senior",
    2: "partner",
    3: "principal",
    4: "investment_manager",
    5: "investment_team",
    6: "associate",
}


def title_priority(title):
    t = str(title).lower()
    for pat, prio in PRIORITY_PATTERNS:
        if re.search(pat, t):
            return prio
    return None


def title_allowed(title):
    t = str(title).lower()
    if not ALLOWED_TITLE_RE.search(t):
        return False
    return not any(w in t for w in EXCLUDE_TITLE_WORDS)


def split_name(full):
    parts = full.split()
    if len(parts) < 2:
        return full, ""
    return " ".join(parts[:-1]), parts[-1]
