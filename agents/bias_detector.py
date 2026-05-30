"""
Algorithmic Bias Detector.
Monitors signal direction distribution and flags when the agent
is becoming biased toward one direction on a pair.
Runs after every scan and sends a Discord warning if bias detected.
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
import os, requests
from dotenv import load_dotenv

load_dotenv()
DB_PATH = Path(__file__).parent.parent / "signals.db"
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

def check_bias(lookback_days: int = 7) -> list:
    """
    Returns list of pairs showing directional bias.
    Bias = more than 75% of signals in one direction over lookback period.
    """
    conn   = sqlite3.connect(DB_PATH)
    c      = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    c.execute("""
        SELECT pair, direction, COUNT(*) as cnt
        FROM signals
        WHERE created_at > ? AND outcome != 'EXPIRED' AND outcome != 'CANCELLED'
        GROUP BY pair, direction
    """, (cutoff,))

    rows    = c.fetchall()
    conn.close()

    # Aggregate by pair
    pair_counts = {}
    for pair, direction, cnt in rows:
        if pair not in pair_counts:
            pair_counts[pair] = {"BUY": 0, "SELL": 0}
        pair_counts[pair][direction] = cnt

    biased = []
    for pair, counts in pair_counts.items():
        total = counts["BUY"] + counts["SELL"]
        if total < 4:  # need at least 4 signals to detect bias
            continue
        buy_pct  = counts["BUY"]  / total * 100
        sell_pct = counts["SELL"] / total * 100
        if buy_pct >= 75:
            biased.append({
                "pair": pair, "bias": "BUY",
                "buy_pct": round(buy_pct), "sell_pct": round(sell_pct),
                "total": total
            })
        elif sell_pct >= 75:
            biased.append({
                "pair": pair, "bias": "SELL",
                "buy_pct": round(buy_pct), "sell_pct": round(sell_pct),
                "total": total
            })

    return biased

def check_performance_by_direction(lookback_days: int = 14) -> dict:
    """
    Track win rates separately for BUY and SELL signals per pair.
    Prevents the agent from abandoning a direction just because
    it had a few losses.
    """
    conn   = sqlite3.connect(DB_PATH)
    c      = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    c.execute("""
        SELECT pair, direction, outcome, COUNT(*) as cnt
        FROM signals
        WHERE created_at > ? AND outcome IN ('WIN','LOSS')
        GROUP BY pair, direction, outcome
    """, (cutoff,))

    rows = c.fetchall()
    conn.close()

    stats = {}
    for pair, direction, outcome, cnt in rows:
        key = f"{pair}_{direction}"
        if key not in stats:
            stats[key] = {"pair": pair, "direction": direction,
                          "wins": 0, "losses": 0}
        if outcome == "WIN":
            stats[key]["wins"]   += cnt
        else:
            stats[key]["losses"] += cnt

    result = {}
    for key, s in stats.items():
        total = s["wins"] + s["losses"]
        if total > 0:
            result[key] = {
                "pair":      s["pair"],
                "direction": s["direction"],
                "wins":      s["wins"],
                "losses":    s["losses"],
                "win_rate":  round(s["wins"] / total * 100, 1),
                "total":     total
            }
    return result

def apply_bias_correction(pair: str, direction: str,
                          confidence: int) -> tuple[int, str]:
    """
    Adjust confidence based on directional bias detection.
    Returns (adjusted_confidence, reason).

    Rules:
    - If pair has >75% bias toward this direction AND
      performance by this direction is WORSE than opposite → reduce confidence 10pts
    - If pair has >75% bias toward opposite direction → agent may be ignoring
      valid setups in this direction — slight boost to ensure it gets through
    - Never reduce below 60 or boost above 90
    """
    biased = check_bias(lookback_days=7)
    perf   = check_performance_by_direction(lookback_days=14)

    pair_bias = next((b for b in biased if b["pair"] == pair), None)
    this_key  = f"{pair}_{direction}"
    opp_dir   = "SELL" if direction == "BUY" else "BUY"
    opp_key   = f"{pair}_{opp_dir}"

    this_wr = perf.get(this_key, {}).get("win_rate", 50)
    opp_wr  = perf.get(opp_key,  {}).get("win_rate", 50)

    reason = ""

    if pair_bias and pair_bias["bias"] == direction:
        # Agent is biased toward this direction already
        if this_wr < opp_wr - 10:
            # This direction is actually underperforming — penalise
            confidence = max(60, confidence - 10)
            reason = (f"Bias correction: {pair} {direction} overrepresented "
                      f"({pair_bias[direction.lower()+'_pct']}% of signals) "
                      f"but win rate ({this_wr}%) below {opp_dir} ({opp_wr}%)")
    elif pair_bias and pair_bias["bias"] == opp_dir:
        # Agent has been ignoring this direction — small boost
        confidence = min(90, confidence + 5)
        reason = (f"Bias correction: {pair} {direction} underrepresented — "
                  f"slight boost to ensure fair evaluation")

    return confidence, reason

def report_bias():
    """Send bias report to Discord if any bias detected."""
    biased = check_bias(lookback_days=7)
    perf   = check_performance_by_direction(lookback_days=14)

    if not biased and not perf:
        return

    lines = ["**📊 Weekly Bias Check**\n"]

    if biased:
        lines.append("⚠️ **Directional Bias Detected:**")
        for b in biased:
            lines.append(
                f"• {b['pair']}: {b['bias']} bias "
                f"({b['buy_pct']}% BUY / {b['sell_pct']}% SELL "
                f"over last 7 days, {b['total']} signals)"
            )
        lines.append("")

    if perf:
        lines.append("**Win Rate by Direction (14 days):**")
        for key, s in sorted(perf.items()):
            lines.append(
                f"• {s['pair']} {s['direction']}: "
                f"{s['win_rate']}% ({s['wins']}W/{s['losses']}L)"
            )

    if WEBHOOK:
        try:
            requests.post(WEBHOOK, json={"content": "\n".join(lines)}, timeout=8)
        except:
            pass

    for b in biased:
        print(f"[bias] ⚠ {b['pair']} {b['bias']} bias: "
              f"{b['buy_pct']}% BUY / {b['sell_pct']}% SELL")

if __name__ == "__main__":
    biased = check_bias()
    if biased:
        print("BIAS DETECTED:")
        for b in biased: print(f"  {b}")
    else:
        print("No bias detected")

    print("\nPerformance by direction:")
    for k, v in check_performance_by_direction().items():
        print(f"  {k}: {v['win_rate']}% WR ({v['total']} signals)")
