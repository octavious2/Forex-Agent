
def expire_old_signals(hours: int = 8):
    """Cancel signals that were never filled after N hours."""
    import sqlite3
    from pathlib import Path
    from datetime import datetime, timezone, timedelta
    DB_PATH = Path(__file__).parent.parent / "signals.db"
    conn    = sqlite3.connect(DB_PATH)
    c       = conn.cursor()
    cutoff  = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c.execute("""
        UPDATE signals SET outcome='EXPIRED'
        WHERE outcome='PENDING' AND created_at < ?
    """, (cutoff,))
    expired = c.rowcount
    conn.commit()
    conn.close()
    if expired > 0:
        print(f"  ⏰ Expired {expired} unfilled signals (>{hours}h old)")
    return expired
"""
Outcome Tracker — checks pending signals against current price.
Runs every hour. Updates win/loss in database.
This is what makes the system learn over time.
"""
from data.price_feed import get_current_price, pip_value
from database.signal_log import get_pending_signals, update_outcome
from notifications.discord import send_signal
from datetime import datetime, timezone

def check_outcomes():
    """Check all pending signals against current prices."""
    pending = get_pending_signals()
    if not pending:
        return

    print(f"\n📋 Checking {len(pending)} pending signals...")

    for sig in pending:
        pair      = sig["pair"]
        direction = sig["direction"]
        sl        = sig["sl"]
        tp1       = sig["tp1"]
        tp2       = sig["tp2"]
        tp3       = sig["tp3"]
        entry     = sig["entry"] or 0

        if not sl or not tp1:
            continue

        price = get_current_price(pair)
        if price == 0:
            continue

        pip   = pip_value(pair)
        outcome = None
        tp_hit  = 0
        pips    = 0

        if direction == "BUY":
            if price <= sl:
                outcome = "LOSS"
                pips    = round((sl - entry) / pip, 1)
            elif tp3 and price >= tp3:
                outcome = "WIN"; tp_hit = 3
                pips    = round((tp3 - entry) / pip, 1)
            elif tp2 and price >= tp2:
                outcome = "WIN"; tp_hit = 2
                pips    = round((tp2 - entry) / pip, 1)
            elif tp1 and price >= tp1:
                outcome = "WIN"; tp_hit = 1
                pips    = round((tp1 - entry) / pip, 1)

        elif direction == "SELL":
            if price >= sl:
                outcome = "LOSS"
                pips    = round((entry - sl) / pip, 1)
            elif tp3 and price <= tp3:
                outcome = "WIN"; tp_hit = 3
                pips    = round((entry - tp3) / pip, 1)
            elif tp2 and price <= tp2:
                outcome = "WIN"; tp_hit = 2
                pips    = round((entry - tp2) / pip, 1)
            elif tp1 and price <= tp1:
                outcome = "WIN"; tp_hit = 1
                pips    = round((entry - tp1) / pip, 1)

        if outcome:
            update_outcome(sig["id"], outcome, tp_hit, pips)
            emoji = "✅" if outcome == "WIN" else "❌"
            print(f"  {emoji} {pair} {direction} → {outcome} "
                  f"(TP{tp_hit}, {pips:+.1f} pips)")

            # Notify Discord of outcome
            _notify_outcome(sig, outcome, tp_hit, pips, price)

def _notify_outcome(sig, outcome, tp_hit, pips, current_price):
    import requests, os
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        return

    emoji = "✅" if outcome == "WIN" else "❌"
    color = 0x00FF88 if outcome == "WIN" else 0xFF4444

    payload = {"embeds": [{
        "title": f"{emoji} OUTCOME: {outcome} — {sig['pair']} {sig['direction']}",
        "color": color,
        "fields": [
            {"name": "Result",        "value": f"`{outcome}` at TP{tp_hit}" if outcome == "WIN" else "`STOPPED OUT`", "inline": True},
            {"name": "Pips",          "value": f"`{pips:+.1f}`", "inline": True},
            {"name": "Current Price", "value": f"`{current_price}`", "inline": True},
        ],
        "footer": {
            "text": f"Signal #{sig['id']} • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        }
    }]}
    try:
        requests.post(webhook, json=payload, timeout=5)
    except:
        pass

if __name__ == "__main__":
    check_outcomes()
