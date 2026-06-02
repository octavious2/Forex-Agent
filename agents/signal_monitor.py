"""
Signal Monitor — runs every 5 minutes.
Tracks active signals and sends Discord updates when:
  - Price is approaching entry zone
  - TP1 / TP2 / TP3 is hit
  - Stop loss is hit
  - Setup is deteriorating (price moving away)
  - Direction change detected
"""
import os
import json
import sqlite3
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from groq import Groq
from dotenv import load_dotenv
from data.price_feed import get_current_price, pip_value, get_candles
from database.signal_log import get_pending_signals, update_outcome

load_dotenv()
WEBHOOK  = os.getenv("DISCORD_WEBHOOK_URL")
GROQ_KEY = os.getenv("GROQ_API_KEY")
client   = Groq(api_key=GROQ_KEY)
DB_PATH  = Path(__file__).parent.parent / "signals.db"

# Track last notification — loaded from DB on startup, persisted after each update
import json as _json
_last_notified = {}

def _load_notified():
    """Load notification state from database on startup."""
    global _last_notified
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("SELECT signal_id, state FROM signal_notify_state")
        for sig_id, state in c.fetchall():
            _last_notified[sig_id] = _json.loads(state)
        conn.close()
    except:
        pass

def _save_notified(sig_id, state):
    """Persist notification state so restarts don't re-fire."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS signal_notify_state
                      (signal_id INTEGER PRIMARY KEY, state TEXT)""")
        c.execute("INSERT OR REPLACE INTO signal_notify_state VALUES (?,?)",
                  (sig_id, _json.dumps(state)))
        conn.commit()
        conn.close()
    except:
        pass

def monitor_all():
    """Check all pending signals and send relevant updates."""
    _load_notified()
    _early_breakeven_check()
    signals = get_pending_signals()
    if not signals:
        return

    now = datetime.now(timezone.utc).strftime("%H:%M")
    print(f"\n📡 [{now}] Monitoring {len(signals)} active signals...")

    for sig in signals:
        try:
            _check_signal(sig)
        except Exception as e:
            print(f"  ⚠ Monitor error {sig['pair']}: {e}")

def _early_breakeven_check():
    """
    For small accounts: move SL to breakeven on any position that reaches
    2% of account profit, locking in gains before TP1. Especially important
    for Gold's large swings. Reads live profit from MT5 status file.
    """
    try:
        from execution.mt5_bridge import get_open_positions
        from agents.risk_manager import get_account_balance, should_move_breakeven
        from notifications.discord import send_status_update
        balance   = get_account_balance()
        positions = get_open_positions()
        for pos in positions:
            profit = float(pos.get("profit", 0))
            symbol = pos.get("symbol", "")
            if should_move_breakeven(symbol, profit, balance):
                # Notify — the EA could later auto-modify, for now alert the trader
                threshold = balance * 0.02
                send_status_update(
                    f"🔒 **{symbol} +${profit:.2f}** (>2% of account)\n"
                    f"Move your stop loss to breakeven NOW to lock this in.\n"
                    f"On a small account, protecting ${profit:.2f} matters more "
                    f"than chasing the full target.",
                    color=0xFFAA00
                )
                print(f"  🔒 {symbol} +${profit:.2f} — breakeven alert sent")
    except Exception as e:
        pass

def _check_signal(sig: dict):
    pair      = sig["pair"]
    direction = sig["direction"]
    entry     = float(sig.get("entry") or 0)
    sl        = float(sig.get("sl")    or 0)
    tp1       = float(sig.get("tp1")   or 0)
    tp2       = float(sig.get("tp2")   or 0)
    tp3       = float(sig.get("tp3")   or 0)
    sig_id    = sig["id"]
    pip       = pip_value(pair)

    if not entry or not sl or not tp1:
        return

    price = get_current_price(pair)
    if price == 0:
        return

    # Distance calculations
    if direction == "BUY":
        pips_to_entry = (entry - price) / pip  if price < entry else 0
        pips_to_sl    = (price - sl)   / pip
        pips_to_tp1   = (tp1 - price)  / pip
        pips_to_tp2   = (tp2 - price)  / pip  if tp2 else 0
        at_entry      = (entry - price) / pip <= 3 and price >= sl
        hit_tp1       = price >= tp1
        hit_tp2       = price >= tp2 if tp2 else False
        hit_tp3       = price >= tp3 if tp3 else False
        hit_sl        = price <= sl
        moving_away   = price < entry - (entry - sl) * 0.3

    else:  # SELL
        pips_to_entry = (price - entry) / pip  if price > entry else 0
        pips_to_sl    = (sl - price)   / pip
        pips_to_tp1   = (price - tp1)  / pip
        pips_to_tp2   = (price - tp2)  / pip  if tp2 else 0
        at_entry      = (price - entry) / pip <= 3 and price <= sl
        hit_tp1       = price <= tp1
        hit_tp2       = price <= tp2 if tp2 else False
        hit_tp3       = price <= tp3 if tp3 else False
        hit_sl        = price >= sl
        moving_away   = price > entry + (sl - entry) * 0.3

    last = _last_notified.get(sig_id, {})

    # ── TP hits ───────────────────────────────────────────────────────
    if hit_tp3 and last.get("tp") != 3:
        pips = round(abs(price - entry) / pip, 1)
        update_outcome(sig_id, "WIN", 3, pips)
        _send_outcome(sig, "WIN", 3, pips, price)
        _last_notified[sig_id] = {**last, "tp": 3}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  🏆 {pair} TP3 HIT! +{pips} pips")
        return

    if hit_tp2 and last.get("tp") != 2:
        pips = round(abs(price - entry) / pip, 1)
        update_outcome(sig_id, "WIN", 2, pips)
        _send_outcome(sig, "WIN", 2, pips, price)
        _last_notified[sig_id] = {**last, "tp": 2}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  ✅ {pair} TP2 HIT! +{pips} pips")
        return

    if hit_tp1 and last.get("tp") != 1:
        pips = round(abs(price - entry) / pip, 1)
        # Don't close — TP1 just means move SL to breakeven
        _send_tp1_update(sig, pips, price, tp2, tp3)
        _last_notified[sig_id] = {**last, "tp": 1}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  ✅ {pair} TP1 HIT — move SL to breakeven")
        return

    # ── SL hit ────────────────────────────────────────────────────────
    if hit_sl and not last.get("sl_hit"):
        pips = round(abs(price - entry) / pip, 1)
        update_outcome(sig_id, "LOSS", 0, -pips)
        _send_outcome(sig, "LOSS", 0, -pips, price)
        _last_notified[sig_id] = {**last, "sl_hit": True}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  ❌ {pair} STOPPED OUT -{pips} pips")
        return

    # ── Approaching entry ─────────────────────────────────────────────
    approach_threshold = 30 if pair == "XAUUSD" else 20 if "JPY" in pair else 12
    if pips_to_entry > 0 and pips_to_entry < approach_threshold and not last.get("near_entry"):
        eta = _estimate_eta(pair, direction, price, entry)
        _send_approaching_entry(sig, price, pips_to_entry, eta)
        _last_notified[sig_id] = {**last, "near_entry": True}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  📍 {pair} approaching entry — {pips_to_entry:.1f} pips away")
        return

    # ── Price already in entry zone ───────────────────────────────────
    if at_entry and not last.get("in_zone"):
        _send_in_zone(sig, price, pips_to_tp1, pips_to_sl)
        _last_notified[sig_id] = {**last, "in_zone": True}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  🎯 {pair} IN ENTRY ZONE — take the trade!")
        return

    # ── Moving away — setup deteriorating ────────────────────────────
    if moving_away and not last.get("warned"):
        analysis = _analyse_deterioration(pair, direction, price, entry, sl)
        _send_warning(sig, price, analysis)
        _last_notified[sig_id] = {**last, "warned": True}
        _save_notified(sig_id, _last_notified[sig_id])
        print(f"  ⚠ {pair} moving away from entry")
        return

    # ── Regular 30-min status update ─────────────────────────────────
    last_update = last.get("status_time")
    if not last_update or _minutes_since(last_update) >= 30:
        progress = _get_progress(direction, price, entry, sl, tp1)
        _send_status(sig, price, pips_to_entry, pips_to_tp1,
                     pips_to_sl, progress)
        _save_notified(sig_id, {**last, "status_time": datetime.now(timezone.utc).isoformat()})
        _last_notified[sig_id] = {
            **last, "status_time": datetime.now(timezone.utc).isoformat()
        }
        print(f"  📊 {pair} status: price={price}, "
              f"{'to entry' if pips_to_entry > 0 else 'to TP1'}="
              f"{pips_to_entry if pips_to_entry > 0 else pips_to_tp1:.1f} pips")

def _estimate_eta(pair: str, direction: str,
                  current_price: float, target: float) -> str:
    """Ask AI how long it might take to reach entry based on recent momentum."""
    try:
        df = get_candles(pair, "15min", 20)
        if df.empty:
            return "ETA unknown"

        # Recent candle range and direction
        recent_moves = []
        for i in range(1, min(6, len(df))):
            move = abs(df["close"].iloc[-i] - df["close"].iloc[-i-1])
            recent_moves.append(move)

        avg_move_per_15m = sum(recent_moves) / len(recent_moves) if recent_moves else 0
        distance = abs(target - current_price)
        pip      = pip_value(pair)

        if avg_move_per_15m > 0:
            candles_needed = distance / avg_move_per_15m
            minutes_needed = candles_needed * 15
            if minutes_needed < 30:
                return f"~{int(minutes_needed)} minutes"
            elif minutes_needed < 120:
                return f"~{int(minutes_needed/60*10)/10} hours"
            else:
                return f"~{int(minutes_needed/60)} hours"
        return "ETA unclear — low momentum"

    except:
        return "ETA unavailable"

def _analyse_deterioration(pair, direction, price, entry, sl) -> str:
    """Ask AI if the setup is still valid or should be cancelled."""
    try:
        df  = get_candles(pair, "15min", 10)
        if df.empty:
            return "Unable to assess — no data"

        candle_text = "\n".join([
            f"  {str(ts)[:16]}  C:{row['close']:.5f}"
            for ts, row in df.tail(5).iterrows()
        ])

        prompt = f"""A {direction} trade was signalled for {pair} at entry {entry}, 
SL {sl}. Current price is {price} which is moving away from entry.

Recent 15M candles:
{candle_text}

In one sentence: Is this setup still valid or should the trader cancel the pending order?
Answer concisely."""

        r = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=80,
        )
        return r.choices[0].message.content.strip()
    except:
        return "Setup assessment unavailable"

def _get_progress(direction, price, entry, sl, tp1) -> str:
    """How far through the trade are we as a percentage."""
    try:
        total_range = abs(tp1 - sl)
        if total_range == 0:
            return "unknown"
        if direction == "BUY":
            progress = (price - sl) / total_range * 100
        else:
            progress = (sl - price) / total_range * 100
        return f"{max(0, min(100, round(progress)))}%"
    except:
        return "unknown"

def _minutes_since(iso_str: str) -> float:
    try:
        t = datetime.fromisoformat(iso_str)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except:
        return 999

# ── Discord senders ───────────────────────────────────────────────────────────

def _discord(payload: dict):
    if not WEBHOOK:
        return
    try:
        r = requests.post(WEBHOOK, json=payload, timeout=8)
        if r.status_code not in (200, 204):
            print(f"  [discord] {r.status_code}")
    except Exception as e:
        print(f"  [discord] {e}")

def _send_outcome(sig, outcome, tp_hit, pips, price):
    emoji = "🏆" if tp_hit == 3 else "✅" if outcome == "WIN" else "❌"
    color = 0x00FF88 if outcome == "WIN" else 0xFF4444
    label = f"TP{tp_hit} HIT" if outcome == "WIN" else "STOPPED OUT"
    _discord({"embeds": [{
        "title": f"{emoji} {outcome}: {sig['pair']} {sig['direction']} — {label}",
        "color": color,
        "fields": [
            {"name": "Result",        "value": f"`{pips:+.1f} pips`", "inline": True},
            {"name": "Close Price",   "value": f"`{price}`",          "inline": True},
            {"name": "Entry was",     "value": f"`{sig['entry']}`",   "inline": True},
        ],
        "footer": {"text": f"Signal #{sig['id']} • {_now()}"}
    }]})

def _send_tp1_update(sig, pips, price, tp2, tp3):
    _discord({"embeds": [{
        "title": f"✅ TP1 HIT — {sig['pair']} {sig['direction']}",
        "color": 0x00FF88,
        "description": (
            f"**+{pips} pips secured.**\n\n"
            f"⚡ **Action required:** Move stop loss to breakeven ({sig['entry']})\n"
            f"🎯 Let the trade run to TP2 (`{tp2}`) and TP3 (`{tp3}`)"
        ),
        "fields": [
            {"name": "Current Price", "value": f"`{price}`",  "inline": True},
            {"name": "Next Target",   "value": f"`{tp2}`",    "inline": True},
            {"name": "New SL",        "value": f"`{sig['entry']}` (breakeven)", "inline": True},
        ],
        "footer": {"text": f"Signal #{sig['id']} • {_now()}"}
    }]})

def _send_approaching_entry(sig, price, pips_away, eta):
    emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
    _discord({"embeds": [{
        "title": f"📍 APPROACHING ENTRY — {sig['pair']} {sig['direction']}",
        "color": 0xFFAA00,
        "description": f"Price is **{pips_away:.1f} pips** from your entry zone.",
        "fields": [
            {"name": "Current Price", "value": f"`{price}`",          "inline": True},
            {"name": "Entry Zone",    "value": f"`{sig['entry']}`",   "inline": True},
            {"name": "ETA",           "value": f"`{eta}`",            "inline": True},
            {"name": "Stop Loss",     "value": f"`{sig['sl']}`",      "inline": True},
            {"name": "TP1",           "value": f"`{sig['tp1']}`",     "inline": True},
            {"name": "Direction",     "value": f"{emoji} `{sig['direction']}`", "inline": True},
        ],
        "footer": {"text": f"Signal #{sig['id']} • Get ready to enter • {_now()}"}
    }]})

def _send_in_zone(sig, price, pips_to_tp1, pips_to_sl):
    emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
    _discord({"embeds": [{
        "title": f"🎯 IN ENTRY ZONE — {sig['pair']} {sig['direction']}",
        "color": 0x00FFAA,
        "description": "**Price is in your entry zone. Consider entering now.**",
        "fields": [
            {"name": "Current Price", "value": f"`{price}`",                "inline": True},
            {"name": "Entry Zone",    "value": f"`{sig['entry']}`",         "inline": True},
            {"name": "Direction",     "value": f"{emoji} `{sig['direction']}`", "inline": True},
            {"name": "Pips to TP1",   "value": f"`{pips_to_tp1:.1f}`",     "inline": True},
            {"name": "Pips to SL",    "value": f"`{pips_to_sl:.1f}`",      "inline": True},
            {"name": "Risk:Reward",   "value": f"`1:{round(pips_to_tp1/pips_to_sl,1) if pips_to_sl>0 else 'N/A'}`", "inline": True},
        ],
        "footer": {"text": f"Signal #{sig['id']} • YOU make the final call • {_now()}"}
    }]})

def _send_warning(sig, price, analysis):
    _discord({"embeds": [{
        "title": f"⚠️ SETUP DETERIORATING — {sig['pair']} {sig['direction']}",
        "color": 0xFF6600,
        "description": f"Price is moving away from your entry.",
        "fields": [
            {"name": "Current Price", "value": f"`{price}`",        "inline": True},
            {"name": "Entry was",     "value": f"`{sig['entry']}`", "inline": True},
            {"name": "AI Assessment", "value": analysis,            "inline": False},
        ],
        "footer": {"text": f"Signal #{sig['id']} • Consider cancelling order • {_now()}"}
    }]})

def _send_status(sig, price, to_entry, to_tp1, to_sl, progress):
    in_trade  = to_entry == 0
    direction = sig["direction"]
    emoji     = "🟢" if direction == "BUY" else "🔴"

    if in_trade:
        title  = f"📊 UPDATE — {sig['pair']} {direction} in progress"
        status = f"Trade running • {progress} of the way to TP1"
        fields = [
            {"name": "Current Price", "value": f"`{price}`",          "inline": True},
            {"name": "Entry",         "value": f"`{sig['entry']}`",   "inline": True},
            {"name": "Progress",      "value": f"`{progress}`",       "inline": True},
            {"name": "To TP1",        "value": f"`{to_tp1:.1f} pips`","inline": True},
            {"name": "To SL",         "value": f"`{to_sl:.1f} pips`", "inline": True},
            {"name": "Direction",     "value": f"{emoji} `{direction}`","inline": True},
        ]
    else:
        title  = f"⏳ WAITING — {sig['pair']} {direction} pending"
        status = f"Order not filled yet"
        fields = [
            {"name": "Current Price", "value": f"`{price}`",            "inline": True},
            {"name": "Entry Zone",    "value": f"`{sig['entry']}`",     "inline": True},
            {"name": "To Entry",      "value": f"`{to_entry:.1f} pips`","inline": True},
            {"name": "SL",            "value": f"`{sig['sl']}`",        "inline": True},
            {"name": "TP1",           "value": f"`{sig['tp1']}`",       "inline": True},
            {"name": "Direction",     "value": f"{emoji} `{direction}`","inline": True},
        ]

    _discord({"embeds": [{
        "title": title,
        "color": 0x5865F2,
        "description": status,
        "fields": fields,
        "footer": {"text": f"Signal #{sig['id']} • {_now()}"}
    }]})

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

if __name__ == "__main__":
    print("Testing signal monitor...")
    monitor_all()
