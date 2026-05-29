import json
import time
from datetime import datetime, timezone
from config.settings import PAIRS, MIN_CONFIDENCE, MIN_RR
from data.price_feed import get_session
from agents.technical import analyse as technical_analyse
from agents.ict_analyst import analyse as ict_analyse
from agents.fundamental import analyse as fundamental_analyse
from agents.trade_advisor import advise
from database.signal_log import log_signal, has_active_signal
from notifications.discord import send_signal, send_heartbeat

_signals_sent  = 0
_pairs_scanned = 0

def run_once():
    global _signals_sent, _pairs_scanned

    session      = get_session()
    now          = datetime.now(timezone.utc).strftime("%H:%M UTC")
    scan_results = []   # collects what happened this scan for memory journal

    print(f"\n{'='*50}")
    print(f"🔍 Scanning {len(PAIRS)} pairs | {now} | {session.upper()}")
    print(f"{'='*50}")

    for pair in PAIRS:

        # Skip pairs with active signals
        if has_active_signal(pair, hours=6):
            print(f"→ {pair} — ⏭  skipping (active signal < 6h)")
            continue

        print(f"\n→ Analysing {pair}...")
        try:
            # 1 — Technical
            print(f"  [1/4] Technical...")
            tech = technical_analyse(pair)
            if "error" in tech:
                print(f"  ⚠ {tech['error']}")
                continue

            trend_4h = tech.get("trend_4h", "unknown")
            trend_1h = tech.get("trend_1h", "unknown")

            # 2 — ICT
            print(f"  [2/4] ICT concepts...")
            ict = ict_analyse(pair, tech)
            if "error" in ict:
                print(f"  ⚠ ICT: {ict.get('error','')[:60]}")
                ict = {"ict_bias": "neutral", "best_setup": {},
                       "pair": pair, "market_structure": {}}

            ict_bias = ict.get("ict_bias", "neutral")

            # 3 — Fundamentals
            print(f"  [3/4] Fundamentals...")
            fund = fundamental_analyse(pair)
            if not fund.get("safe_to_trade", True):
                print(f"  🚫 BLOCKED — {fund.get('avoid_reason','News event')}")
                continue

            # 4 — Trade Advisor
            print(f"  [4/4] Trade Advisor...")
            signal = advise(pair, tech, ict, session)
            signal["session"]  = session
            signal["trend_4h"] = trend_4h
            signal["ict_bias"] = ict_bias

            decision   = signal.get("decision", "WAIT")
            confidence = signal.get("confidence", 0)
            rr         = float(signal.get("rr_ratio") or 0)

            # HTF alignment check
            if not _htf_allows(trend_4h, trend_1h, ict_bias, decision):
                print(f"  📊 {decision} | Conf: {confidence} | RR: 1:{rr}")
                print(f"  🚫 HTF FILTERED — 4H={trend_4h} ICT={ict_bias} "
                      f"conflicts with {decision}")
                scan_results.append({**signal, "pair": pair, "filtered": True})
                continue

            print(f"  📊 {decision} | Confidence: {confidence}/100 | RR: 1:{rr}")

            # Collect for journal regardless of outcome
            scan_results.append({
                "pair":       pair,
                "decision":   decision,
                "confidence": confidence,
                "rr_ratio":   rr,
                "price":      tech.get("current_price", 0),
                "trend_4h":   trend_4h,
                "trend_1h":   trend_1h,
                "ict_bias":   ict_bias,
                "setup_type": signal.get("setup_type", ""),
            })

            if (decision in ["BUY", "SELL"]
                    and confidence >= MIN_CONFIDENCE
                    and rr >= MIN_RR):

                signal_id = log_signal({
                    "pair":       pair,
                    "direction":  decision,
                    "entry_low":  signal.get("entry_low"),
                    "entry_high": signal.get("entry_high"),
                    "stop_loss":  signal.get("stop_loss"),
                    "tp1":        signal.get("tp1"),
                    "tp2":        signal.get("tp2"),
                    "tp3":        signal.get("tp3"),
                    "confidence": confidence,
                    "rr_ratio":   rr,
                    "session":    session,
                    "setup_type": signal.get("setup_type", "confluence"),
                    "analysis":   json.dumps(signal.get("reasoning", {})),
                })
                send_signal(signal)
                _execute_on_mt5(signal, session)
                _signals_sent += 1
                print(f"  ✅ Signal sent to Discord! (ID: {signal_id})")
            else:
                print(f"  ⏸  WAIT — conf={confidence} rr={rr} "
                      f"(need ≥{MIN_CONFIDENCE} / ≥{MIN_RR})")

            _pairs_scanned += 1
            time.sleep(3)

        except Exception as e:
            print(f"  ❌ {pair} failed: {e}")
            import traceback; traceback.print_exc()
            continue

    # Write memory journal after every scan
    if scan_results:
        try:
            from agents.memory import write_journal
            write_journal(session, scan_results)
        except Exception as e:
            print(f"[memory] Journal error: {e}")

    print(f"\n✅ Scan done — {_pairs_scanned} pairs | {_signals_sent} total signals")

def _htf_allows(trend_4h, trend_1h, ict_bias, decision) -> bool:
    if decision == "WAIT":
        return True
    if trend_4h in ("ranging", "unknown") and trend_1h in ("ranging","unknown"):
        return True
    buy_score = sell_score = 0
    if trend_4h in ("bullish","bullish_pullback"):   buy_score  += 2
    elif trend_4h in ("bearish","bearish_pullback"): sell_score += 2
    if trend_1h in ("bullish","bullish_pullback"):   buy_score  += 1
    elif trend_1h in ("bearish","bearish_pullback"): sell_score += 1
    if ict_bias == "bullish":   buy_score  += 1
    elif ict_bias == "bearish": sell_score += 1
    return buy_score >= sell_score if decision=="BUY" else sell_score >= buy_score

def daily_heartbeat():
    send_heartbeat(_pairs_scanned, _signals_sent)

if __name__ == "__main__":
    run_once()

def _execute_on_mt5(signal: dict, session: str):
    """Execute signal on MT5 demo account via file bridge."""
    try:
        from execution.mt5_bridge import send_trade, check_mt5_running
        if not check_mt5_running():
            print(f"  ⚠ MT5 EA not running — signal sent to Discord only")
            return
        # Use 0.01 lot for demo testing
        result = send_trade(signal, lot_size=0.01)
        status = result.get("status", "unknown")
        ticket = result.get("ticket", 0)
        if status == "executed":
            print(f"  🤖 MT5 EXECUTED: ticket #{ticket}")
            from notifications.discord import _discord
            _discord({"embeds": [{
                "title": f"🤖 MT5 DEMO EXECUTED — {signal['pair']} {signal.get('decision','')}",
                "color": 0x00FF88,
                "fields": [
                    {"name": "Ticket",  "value": f"`#{ticket}`",             "inline": True},
                    {"name": "Lot",     "value": "`0.01`",                   "inline": True},
                    {"name": "Price",   "value": f"`{result.get('price','')}` ", "inline": True},
                ],
                "footer": {"text": "Demo account — paper trading"}
            }]})
        else:
            print(f"  ⚠ MT5 execution: {status} — {result.get('message','')}")
    except Exception as e:
        print(f"  ⚠ MT5 bridge error: {e}")
