"""
Weekend BTC Scanner.
Runs Saturday and Sunday until 22:00 UTC when forex closes.
Scans BTC/USD every 30 minutes using the same agent pipeline.
BTC trades 24/7 — no session filter, no news calendar block.
"""
import json
import time
from datetime import datetime, timezone
from config.settings import MIN_CONFIDENCE, MIN_RR
from data.price_feed import get_candles, get_current_price, pip_value

BTC_PAIR = "BTCUSD"

# BTC-specific risk settings — higher volatility needs wider parameters
BTC_MIN_CONFIDENCE = 70    # stricter than forex
BTC_MIN_RR         = 2.0   # higher RR required — BTC moves are big
BTC_LOT_SIZE       = 0.001 # minimum lot for BTC on most brokers

def run_btc_scan():
    """Full BTC/USD analysis scan."""
    now = datetime.now(timezone.utc)
    print(f"\n{'='*50}")
    print(f"₿  BTC/USD Weekend Scan | {now.strftime('%H:%M UTC')}")
    print(f"{'='*50}")

    try:
        # Step 1 — Technical analysis
        print("  [1/3] Technical analysis...")
        from agents.technical import analyse as ta
        tech = ta(BTC_PAIR)
        if "error" in tech:
            print(f"  ⚠ Technical error: {tech['error']}")
            return

        price = tech.get("current_price", 0)
        print(f"  BTC price: ${price:,.2f}")

        # Step 2 — ICT analysis (same agent, BTC has clear institutional structure)
        print("  [2/3] ICT concepts...")
        from agents.ict_analyst import analyse as ict
        ict_result = ict(BTC_PAIR, tech)

        # Step 3 — Trade Advisor (no fundamental block for BTC — 24/7 market)
        print("  [3/3] Trade Advisor...")
        from agents.trade_advisor import advise
        signal = advise(BTC_PAIR, tech, ict_result, "crypto_weekend",
                        account_balance=200.0)

        decision   = signal.get("decision", "WAIT")
        confidence = signal.get("confidence", 0)
        rr         = float(signal.get("rr_ratio") or 0)

        print(f"  📊 {decision} | Confidence: {confidence}/100 | RR: 1:{rr}")

        if (decision in ["BUY", "SELL"]
                and confidence >= BTC_MIN_CONFIDENCE
                and rr >= BTC_MIN_RR):

            signal["session"] = "crypto_weekend"

            # Log to database
            from database.signal_log import log_signal, has_active_signal
            if has_active_signal(BTC_PAIR, hours=4):
                print(f"  ⏭ BTC active signal exists — skipping")
                return

            signal_id = log_signal({
                "pair":       BTC_PAIR,
                "direction":  decision,
                "entry_low":  signal.get("entry_low"),
                "entry_high": signal.get("entry_high"),
                "stop_loss":  signal.get("stop_loss"),
                "tp1":        signal.get("tp1"),
                "tp2":        signal.get("tp2"),
                "tp3":        signal.get("tp3"),
                "confidence": confidence,
                "rr_ratio":   rr,
                "session":    "crypto_weekend",
                "setup_type": signal.get("setup_type", "order_block"),
                "analysis":   json.dumps(signal.get("reasoning", {})),
            })

            # Send to Discord
            from notifications.discord import send_signal
            signal["pair"]    = BTC_PAIR
            signal["session"] = "crypto_weekend"
            send_signal(signal)
            print(f"  ✅ BTC signal sent to Discord (ID: {signal_id})")

            # Execute on MT5 if running
            from execution.mt5_bridge import check_mt5_running, send_trade
            if check_mt5_running():
                signal["id"] = signal_id
                result = send_trade(signal, lot_size=BTC_LOT_SIZE)
                status = result.get("status", "unknown")
                if status == "placed":
                    print(f"  🤖 MT5: {result.get('message','')}")
                else:
                    print(f"  ⚠ MT5: {status} — {result.get('message','')}")
        else:
            print(f"  ⏸  WAIT — conf={confidence} rr={rr} "
                  f"(need ≥{BTC_MIN_CONFIDENCE} / ≥{BTC_MIN_RR})")

        # Write to memory journal
        try:
            from agents.memory import write_journal
            write_journal("crypto_weekend", [{
                "pair":       BTC_PAIR,
                "decision":   decision,
                "confidence": confidence,
                "rr_ratio":   rr,
                "price":      price,
                "trend_4h":   tech.get("trend_4h", ""),
                "trend_1h":   tech.get("trend_1h", ""),
                "ict_bias":   ict_result.get("ict_bias", "neutral"),
                "setup_type": signal.get("setup_type", ""),
            }])
        except Exception as e:
            print(f"  [memory] {e}")

    except Exception as e:
        print(f"  ❌ BTC scan error: {e}")
        import traceback; traceback.print_exc()

if __name__ == "__main__":
    run_btc_scan()
