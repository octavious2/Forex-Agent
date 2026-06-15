
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

def _sane_pips(raw_pips, pair):
    """Reject impossible pip values caused by null/zero entry prices."""
    limits = {"XAUUSD": 5000, "BTCUSD": 50000}
    cap = limits.get(pair, 1000)  # forex pairs never move 1000+ pips intraday
    try:
        v = float(raw_pips)
    except (ValueError, TypeError):
        return 0.0
    if abs(v) > cap:
        print(f"  ⚠ Rejected impossible pip value {v} for {pair} — entry was likely null")
        return 0.0
    return round(v, 1)



def _load_closed_deals():
    """Read MT5's actual closed deals from the status file. Keyed by position id."""
    try:
        import json
        from pathlib import Path
        sf = Path.home() / ".mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files/lifetap_status.json"
        data = json.loads(sf.read_text())
        out = {}
        for d in data.get("closed_deals", []):
            out[int(d.get("position", 0))] = d
        return out
    except Exception:
        return {}


def check_outcomes():
    """Check all pending signals against current prices."""
    pending = get_pending_signals()
    if not pending:
        return

    print(f"\n📋 Checking {len(pending)} pending signals...")

    closed_deals = _load_closed_deals()
    for sig in pending:
        pair      = sig["pair"]
        direction = sig["direction"]
        sl        = sig["sl"]
        tp1       = sig["tp1"]
        tp2       = sig["tp2"]
        tp3       = sig["tp3"]
        entry     = sig["entry"] or 0
        ticket    = sig.get("mt5_ticket", 0) or 0

        # REAL OUTCOME from MT5 closed-deal history — authoritative, no guessing.
        if ticket and int(ticket) in closed_deals:
            deal   = closed_deals[int(ticket)]
            profit = float(deal.get("profit", 0))
            outcome = "WIN" if profit > 0 else "LOSS"
            close_px = float(deal.get("price", 0))
            pip = pip_value(pair)
            if entry and close_px and pip:
                raw_pips = ((close_px - entry) / pip) if direction == "BUY" else ((entry - close_px) / pip)
                pips = _sane_pips(raw_pips, pair)
            else:
                pips = 0
            update_outcome(sig["id"], outcome, 0, pips)
            _notify_outcome(sig, outcome, 0, pips, close_px)
            print(f"  {'🏆' if outcome=='WIN' else '❌'} {pair} {outcome} "
                  f"${profit:+.2f} ({pips:+.1f} pips) — from MT5 history")
            continue

        if not sl or not tp1 or not entry:
            continue

        # FILL VERIFICATION: never record an outcome for a signal that did not
        # actually fill in MT5. Prevents phantom wins/losses on errored or
        # unfilled orders (e.g. "Symbol not found", limit never triggered).
        try:
            from execution.mt5_bridge import get_open_positions, check_mt5_running
            if check_mt5_running():
                open_syms = [p.get("symbol") for p in get_open_positions()]
                ticket    = sig.get("mt5_ticket", 0) or 0
                if pair not in open_syms and ticket == 0:
                    continue  # never filled — skip, let it expire
        except Exception:
            pass

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
                pips    = _sane_pips((sl - entry) / pip, sig["pair"])
            elif tp3 and price >= tp3:
                outcome = "WIN"; tp_hit = 3
                pips    = _sane_pips((tp3 - entry) / pip, sig["pair"])
            elif tp2 and price >= tp2:
                outcome = "WIN"; tp_hit = 2
                pips    = _sane_pips((tp2 - entry) / pip, sig["pair"])
            elif tp1 and price >= tp1:
                outcome = "WIN"; tp_hit = 1
                pips    = _sane_pips((tp1 - entry) / pip, sig["pair"])

        elif direction == "SELL":
            if price >= sl:
                outcome = "LOSS"
                pips    = _sane_pips((entry - sl) / pip, sig["pair"])
            elif tp3 and price <= tp3:
                outcome = "WIN"; tp_hit = 3
                pips    = _sane_pips((entry - tp3) / pip, sig["pair"])
            elif tp2 and price <= tp2:
                outcome = "WIN"; tp_hit = 2
                pips    = _sane_pips((entry - tp2) / pip, sig["pair"])
            elif tp1 and price <= tp1:
                outcome = "WIN"; tp_hit = 1
                pips    = _sane_pips((entry - tp1) / pip, sig["pair"])

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
