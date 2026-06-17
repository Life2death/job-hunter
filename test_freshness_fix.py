"""
Isolated test of the fixed freshness() logic against realistic Naukri payload shapes.
Run: python3 test_freshness_fix.py
"""
import re
from datetime import date, datetime

TODAY = date(2026, 6, 17)
_RE_DAYS = re.compile(r"\d+")
FRESH_MAX = 3
AGING_MAX = 7


def freshness(posted_str):
    if posted_str is None or posted_str == "":
        return "UNKNOWN", -10, None

    if isinstance(posted_str, (int, float)):
        s_raw = str(int(posted_str))
    else:
        s_raw = str(posted_str).strip()

    if not s_raw:
        return "UNKNOWN", -10, None

    s = s_raw.lower()
    age = None

    if s_raw.isdigit() and len(s_raw) >= 9:
        ts = int(s_raw)
        ts = ts / 1000 if ts > 1e11 else ts
        try:
            age = (TODAY - datetime.fromtimestamp(ts).date()).days
        except (ValueError, OverflowError, OSError):
            age = None

    if age is None:
        iso_candidate = s_raw.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(iso_candidate)
            age = (TODAY - d.date()).days
        except ValueError:
            pass

    if age is None:
        try:
            d = datetime.strptime(s_raw[:10], "%Y-%m-%d").date()
            age = (TODAY - d).days
        except ValueError:
            pass

    if age is None:
        if any(x in s for x in ["just", "today", "now", "few hours", "hour"]):
            age = 0
        elif "yesterday" in s:
            age = 1
        elif "week" in s:
            m = _RE_DAYS.search(s)
            age = int(m.group()) * 7 if m else None
        elif "month" in s:
            m = _RE_DAYS.search(s)
            age = int(m.group()) * 30 if m else 99
        elif "day" in s:
            m = _RE_DAYS.search(s)
            age = int(m.group()) if m else None

    if age is None:
        return "UNKNOWN", -10, None
    if age < 0:
        age = 0

    if age <= FRESH_MAX:
        return "FRESH", 0, age
    elif age <= AGING_MAX:
        return "AGING", -10, age
    return "STALE", None, age


TEST_CASES = [
    # (input, expected_tag, description)
    ("2026-06-08T06:00:00.000Z", "STALE", "Naukri ISO with Z + millis (9 days old)"),
    ("2026-06-08T06:00:00", "STALE", "ISO without Z"),
    ("2026-06-15", "FRESH", "plain date, 2 days old (<=FRESH_MAX=3)"),
    ("2026-06-17", "FRESH", "plain date, today"),
    (1780898400000, "STALE", "epoch millis as int, 2026-06-08 = 9 days before 2026-06-17"),
    ("1780898400000", "STALE", "epoch millis as string"),
    ("1 week ago", "AGING", "relative text, 7 days = AGING boundary"),
    ("2 weeks ago", "STALE", "relative text, 14 days"),
    ("Just now", "FRESH", "relative text, 0 days"),
    ("Yesterday", "FRESH", "relative text, 1 day"),
    ("3 days ago", "FRESH", "relative text, 3 days = FRESH boundary"),
    ("4 days ago", "AGING", "relative text, 4 days"),
    ("", "UNKNOWN", "empty string"),
    (None, "UNKNOWN", "None"),
    ("garbage_no_signal", "UNKNOWN", "unparseable junk"),
]

print(f"{'INPUT':35s} {'EXPECTED':10s} {'GOT':10s} {'AGE':6s} RESULT")
print("-" * 80)
all_pass = True
for inp, expected, desc in TEST_CASES:
    tag, mod, age = freshness(inp)
    ok = "OK" if tag == expected else "MISMATCH"
    if tag != expected:
        all_pass = False
    print(f"{str(inp)[:34]:35s} {expected:10s} {tag:10s} {str(age):6s} {ok}   # {desc}")

print("\nAll pass:" , all_pass) 
