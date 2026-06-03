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
from agents.risk_manager import calculate_lot_size, get_account_balance, should_move_breakeven
from notifications.discord import send_signal, send_heartbeat

_signals_sent  = 0
_pairs_scanned = 0

# Rotating index so each cycle analyses ONE pair deeply instead of rushing all
_pair_rotation_index = 0

# Raised confidence bar — only act on genuinely strong setups
DEEP_MIN_CONFIDENCE = 75   # was 65 — fewer, higher-quality trades

def run_once():
    global _signals_sent, _pairs_scanned

    # Weekend check — forex closed Saturday and most of Sunday
    from datetime import datetime, timezone
    now     = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Monday, 5=Saturday, 6=Sunday
    hour    = now.hour

    if weekday == 5:  # Saturday — forex closed, run BTC instead
        print(f"\n₿  Saturday — forex closed. Running BTC/USD weekend scan...")
        from agents.weekend_btc import run_btc_scan
        run_btc_scan()
        return
    if weekday == 6 and hour < 22:  # Sunday before 22:00 UTC — BTC mode
        print(f"\n₿  Sunday — forex opens 22:00 UTC. Running BTC weekend scan...")
        from agents.weekend_btc import run_btc_scan
        run_btc_scan()
        return
    if weekday == 4 and hour >= 16:  # Friday after 16:00 UTC
        print(f"\n💤 Friday afternoon — avoiding weekend gap risk.")
        return

    # EA health check — alert if MT5 EA has stopped writing
    try:
        from execution.mt5_bridge import check_mt5_running
        from notifications.discord import send_status_update
        if not check_mt5_running():
            send_status_update(
                "⚠️ **MT5 EA not responding** — trades are NOT being executed.\n"
                "Check that MetaTrader 5 is open, the chart with LifeTapEA is active, "
                "and Algo Trading is enabled (green button).",
                color=0xFF0000
            )
            print("  ⚠ MT5 EA STOPPED — execution paused, Discord alerted")
    except:
        pass

    session      = get_session()

    # London open trap window: 07:00-08:00 UTC is the highest manipulation period
    # Banks fake breakouts to collect liquidity before the real move
    # Require the first 45 minutes to pass before trusting London signals
    from datetime import datetime, timezone
    utc_now = datetime.now(timezone.utc)
    if utc_now.hour == 7 and utc_now.minute < 45:
        print(f"  ⏸ London open trap window — waiting until 07:45 UTC for manipulation to clear")
        return
    now          = datetime.now(timezone.utc).strftime("%H:%M UTC")
    scan_results = []   # collects what happened this scan for memory journal

    print(f"\n{'='*50}")
    print(f"🔍 Scanning {len(PAIRS)} pairs | {now} | {session.upper()}")
    print(f"{'='*50}")

    # ═══════════════════════════════════════════════════════════════
    # AGENTIC LOOP: survey all pairs → screen → rank → verify best 2 → trade
    # ═══════════════════════════════════════════════════════════════
    print(f"\n🔍 Surveying {len(PAIRS)} pairs | {now} | {session.upper()}")
    print(f"{'='*50}")

    candidates = []   # pairs that pass the basic screen

    for pair in PAIRS:
        # Skip pairs that already have an active position/signal
        if has_active_signal(pair, hours=6):
            print(f"→ {pair}: ⏭  already has active signal, skipping")
            continue

        print(f"\n→ Screening {pair}...")
        try:
            tech = technical_analyse(pair)
            if "error" in tech:
                print(f"  ⚠ {tech['error']} — skipping")
                continue
            trend_4h = tech.get("trend_4h", "unknown")
            trend_1h = tech.get("trend_1h", "unknown")

            ict = ict_analyse(pair, tech)
            if "error" in ict:
                ict = {"ict_bias": "neutral", "best_setup": {},
                       "pair": pair, "market_structure": {}}
            ict_bias = ict.get("ict_bias", "neutral")

            fund = fundamental_analyse(pair)
            if not fund.get("safe_to_trade", True):
                print(f"  🚫 {pair}: BLOCKED — {fund.get('avoid_reason','news')} — moving to next pair")
                continue

            signal = advise(pair, tech, ict, session)
            signal["session"]  = session
            signal["trend_4h"] = trend_4h
            signal["ict_bias"] = ict_bias
            decision   = signal.get("decision", "WAIT")
            try:
                confidence = int(float(signal.get("confidence", 0) or 0))
            except (ValueError, TypeError):
                confidence = 0
            signal["confidence"] = confidence
            try:
                rr = float(signal.get("rr_ratio") or 0)
            except (ValueError, TypeError):
                rr = 0.0

            scan_results.append({
                "pair": pair, "decision": decision, "confidence": confidence,
                "rr_ratio": rr, "price": tech.get("current_price", 0),
                "trend_4h": trend_4h, "trend_1h": trend_1h,
                "ict_bias": ict_bias, "setup_type": signal.get("setup_type", ""),
            })
            _pairs_scanned += 1

            # HTF alignment gate
            if not _htf_allows(trend_4h, trend_1h, ict_bias, decision):
                print(f"  📊 {decision} {confidence}/100 — 🚫 HTF conflict, not a candidate")
                continue

            # Basic screen — must clear the bar to become a candidate
            if decision in ("BUY", "SELL") and confidence >= DEEP_MIN_CONFIDENCE and rr >= MIN_RR:
                score = confidence * rr   # quality score for ranking
                candidates.append({
                    "pair": pair, "signal": signal, "tech": tech, "ict": ict,
                    "confidence": confidence, "rr": rr, "score": score,
                    "decision": decision,
                })
                print(f"  ✅ {pair}: CANDIDATE — {decision} {confidence}/100 RR 1:{rr} (score {score:.0f})")
            else:
                print(f"  📊 {pair}: {decision} {confidence}/100 RR 1:{rr} — below bar, not a candidate")

            time.sleep(2)
        except Exception as e:
            print(f"  ❌ {pair} screen failed: {e}")
            continue

    # ── RANK all candidates, then fall through verifying until 2 approved ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    MAX_TRADES = 2   # approve at most this many per cycle

    if not candidates:
        print(f"\n🤔 No qualifying setups across any pair this cycle. Waiting.")
    else:
        print(f"\n🎯 {len(candidates)} candidate(s) found. Verifying best-first "
              f"until {MAX_TRADES} approved or list exhausted:")

    from agents.trade_advisor import deep_verify
    approved_count = 0
    for cand in candidates:
        if approved_count >= MAX_TRADES:
            print(f"  ✋ Reached {MAX_TRADES} approved trades — stopping for this cycle")
            break
        pair   = cand["pair"]
        signal = cand["signal"]
        print(f"\n🔬 Deep-verifying {pair} ({cand['decision']} {cand['confidence']}/100)...")
        approved, reason = deep_verify(pair, signal, cand["tech"], cand["ict"])
        if not approved:
            print(f"  🛑 {pair} VETOED — {reason} → moving to next candidate")
            continue
        print(f"  ✅ {pair} APPROVED — {reason}")
        approved_count += 1

        decision   = cand["decision"]
        confidence = cand["confidence"]
        rr         = cand["rr"]
        signal_id = log_signal({
            "pair": pair, "direction": decision,
            "entry_low": signal.get("entry_low"), "entry_high": signal.get("entry_high"),
            "stop_loss": signal.get("stop_loss"),
            "tp1": signal.get("tp1"), "tp2": signal.get("tp2"), "tp3": signal.get("tp3"),
            "confidence": confidence, "rr_ratio": rr, "session": session,
            "setup_type": signal.get("setup_type", "order_block"),
            "analysis": json.dumps(signal.get("reasoning", {})),
        })
        send_signal(signal)
        _execute_on_mt5(signal, session, signal_id)
        _signals_sent += 1
        print(f"  ✅ Signal sent & executed (ID: {signal_id})")
        time.sleep(2)

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

def _execute_on_mt5(signal: dict, session: str, signal_id: int = 0):
    """Execute signal on MT5 demo account via file bridge."""
    try:
        from execution.mt5_bridge import send_trade, check_mt5_running
        if not check_mt5_running():
            print(f"  ⚠ MT5 EA not running — signal sent to Discord only")
            return
        # Use 0.01 lot for demo testing
        signal["id"] = signal_id
        # Size the position from live account balance and 1% risk
        balance = get_account_balance()
        entry   = float(signal.get("entry_low") or signal.get("entry_high") or signal.get("price") or 0)
        sl      = float(signal.get("stop_loss") or 0)
        lot     = calculate_lot_size(balance, entry, sl, signal["pair"])
        print(f"  💰 Account ${balance:.2f} → lot {lot} (1% risk)")
        result = send_trade(signal, lot_size=lot)
        status = result.get("status", "unknown")
        ticket = result.get("ticket", 0)

        # Link this signal to its real MT5 ticket so outcomes track the
        # actual position, not the theoretical price. Only filled/placed
        # orders get a ticket; phantom price-touches never will.
        if ticket and signal_id:
            try:
                from database.signal_log import set_ticket
                set_ticket(signal_id, ticket)
            except Exception as e:
                print(f"  ⚠ could not store ticket: {e}")

        if status in ("executed", "placed"):
            label = "EXECUTED" if status == "executed" else "ORDER PLACED"
            print(f"  🤖 MT5 {label}: ticket #{ticket}")
            from notifications.discord import send_discord
            send_discord({"embeds": [{
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

def _apply_bias_correction(pair: str, decision: str, confidence: int) -> tuple:
    """Apply bias correction before sending signal."""
    try:
        from agents.bias_detector import apply_bias_correction
        return apply_bias_correction(pair, decision, confidence)
    except Exception as e:
        return confidence, ""

# Correlation map — pairs that move together
CORRELATED = {
    "EURUSD": ["GBPUSD", "AUDUSD"],
    "GBPUSD": ["EURUSD", "GBPJPY"],
    "GBPJPY": ["GBPUSD"],
    "AUDUSD": ["EURUSD"],
    "USDJPY": ["GBPJPY"],
}

def _has_correlated_active(pair: str, direction: str) -> bool:
    """
    Returns True if a correlated pair already has an active signal
    in the SAME direction — prevents double exposure.
    """
    related = CORRELATED.get(pair, [])
    if not related:
        return False
    try:
        from database.signal_log import get_pending_signals
        pending = get_pending_signals()
        for sig in pending:
            if sig["pair"] in related and sig["direction"] == direction:
                print(f"  ⚠ Correlation block: {pair} {direction} "
                      f"correlates with active {sig['pair']} {sig['direction']}")
                return True
    except:
        pass
    return False
