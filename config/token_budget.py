"""
Daily token budget tracker.
Stops non-critical scans when approaching the Groq free tier limit.
Resets automatically at midnight UTC.
"""
import json
from datetime import datetime, timezone, date
from pathlib import Path

BUDGET_FILE  = Path(__file__).parent.parent / "token_budget.json"
DAILY_LIMIT  = 95000   # leave 5K buffer before the 100K limit
WARNING_AT   = 80000   # warn when 80% used

def _load():
    try:
        if BUDGET_FILE.exists():
            data = json.loads(BUDGET_FILE.read_text())
            if data.get("date") == str(date.today()):
                return data
    except:
        pass
    return {"date": str(date.today()), "used": 0}

def _save(data):
    try:
        BUDGET_FILE.write_text(json.dumps(data))
    except:
        pass

def add_tokens(count: int):
    data = _load()
    data["used"] += count
    _save(data)

def get_used() -> int:
    return _load().get("used", 0)

def is_budget_ok() -> bool:
    return get_used() < DAILY_LIMIT

def get_remaining() -> int:
    return max(0, DAILY_LIMIT - get_used())

def status() -> str:
    used = get_used()
    pct  = round(used / DAILY_LIMIT * 100)
    return f"Tokens today: {used:,}/{DAILY_LIMIT:,} ({pct}%)"

if __name__ == "__main__":
    print(status())
    print(f"Budget OK: {is_budget_ok()}")
    print(f"Remaining: {get_remaining():,}")
