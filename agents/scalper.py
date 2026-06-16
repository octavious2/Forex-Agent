"""
Scalp Signal Agent — momentum-based scalping on lower timeframes.
For tight-spread pairs only (EURUSD, GBPUSD, USDJPY). Uses 5M/15M candles,
tight spread-adjusted targets, and rejects setups where spread eats the edge.
"""
from data.price_feed import get_candles, get_current_price, get_spread, pip_value
from agents.technical import _trend, _indicators, _market_structure
from config.settings import (SCALP_TARGET_PIPS, SCALP_STOP_PIPS,
                             SCALP_MAX_SPREAD_RATIO)


def scalp_signal(pair: str) -> dict:
    """
    Momentum scalp on 5M confirmed by 15M direction.
    Returns BUY/SELL/WAIT with tight, spread-adjusted levels.
    """
    pip = pip_value(pair)

    # 1 — Spread gate FIRST. No point analysing if spread eats the target.
    spread = get_spread(pair)
    max_spread = SCALP_TARGET_PIPS * SCALP_MAX_SPREAD_RATIO
    if spread > max_spread:
        return {"pair": pair, "decision": "WAIT",
                "reason": f"spread {spread} > max {max_spread:.1f} for scalp"}

    # 2 — Pull fast candles
    df_15m = get_candles(pair, "15min", 70)
    df_5m  = get_candles(pair, "5min", 70)
    if df_5m.empty or df_15m.empty:
        return {"pair": pair, "decision": "WAIT", "reason": "no fast candle data"}

    # 3 — Momentum read: 15M sets direction, 5M confirms
    trend_15m = _trend(df_15m)
    trend_5m  = _trend(df_5m)
    ind_5m    = _indicators(df_5m)
    price     = get_current_price(pair)
    if price == 0:
        price = float(df_5m["close"].iloc[-1])

    rsi   = ind_5m.get("rsi", 50)
    macd  = ind_5m.get("macd_cross", "neutral")

    # Direction: both timeframes must agree (momentum alignment)
    bullish = (trend_15m in ("bullish", "bullish_pullback")
               and trend_5m in ("bullish", "bullish_pullback")
               and macd == "bullish" and rsi < 70)
    bearish = (trend_15m in ("bearish", "bearish_pullback")
               and trend_5m in ("bearish", "bearish_pullback")
               and macd == "bearish" and rsi > 30)

    if not (bullish or bearish):
        return {"pair": pair, "decision": "WAIT",
                "reason": f"no aligned momentum (15M={trend_15m} 5M={trend_5m} rsi={rsi:.0f})"}

    decision = "BUY" if bullish else "SELL"

    # Real confidence from setup strength (not hardcoded)
    conf = 50
    # Strong trend (not just pullback) on both timeframes
    strong_15 = trend_15m in ("bullish", "bearish")
    strong_5  = trend_5m in ("bullish", "bearish")
    if strong_15 and strong_5:
        conf += 15          # both firmly trending
    elif strong_15 or strong_5:
        conf += 7           # one firm, one pullback
    # RSI momentum in trade direction (further from 50 = stronger)
    if decision == "BUY":
        conf += min(15, max(0, int((rsi - 50) * 0.6)))   # rsi 50->0pts, 75->15pts
    else:
        conf += min(15, max(0, int((50 - rsi) * 0.6)))   # rsi 50->0pts, 25->15pts
    # MACD already required to confirm — small fixed credit
    conf += 10
    # Spread tightness: tighter than target is better
    if spread <= SCALP_TARGET_PIPS * 0.05:
        conf += 10
    elif spread <= SCALP_TARGET_PIPS * 0.15:
        conf += 5
    conf = max(50, min(95, conf))

    # 4 — Tight levels, spread-adjusted. Target must clear spread.
    target_pips = SCALP_TARGET_PIPS + spread   # need to cover spread to net target
    stop_pips   = SCALP_STOP_PIPS

    if decision == "BUY":
        entry = price
        sl    = round(price - stop_pips * pip, 5)
        tp1   = round(price + target_pips * pip, 5)
    else:
        entry = price
        sl    = round(price + stop_pips * pip, 5)
        tp1   = round(price - target_pips * pip, 5)

    rr = round(target_pips / stop_pips, 2)

    return {
        "pair": pair,
        "decision": decision,
        "confidence": conf,   # derived from trend strength + RSI + spread
        "entry_low": entry, "entry_high": entry,
        "stop_loss": sl, "tp1": tp1,
        "tp2": tp1, "tp3": tp1,   # scalp = single target, close full at TP1
        "rr_ratio": rr,
        "setup_type": "scalp_momentum",
        "spread_at_signal": spread,
        "reasoning": {
            "why_enter": f"Momentum scalp: 15M {trend_15m}, 5M {trend_5m}, "
                         f"MACD {macd}, RSI {rsi:.0f}. Spread {spread} pips.",
            "main_risk": "Momentum scalps fail on sudden reversals; tight stop.",
        },
    }


if __name__ == "__main__":
    for p in ["EURUSD", "GBPUSD", "USDJPY"]:
        s = scalp_signal(p)
        print(f"{p}: {s['decision']} — {s.get('reason', s.get('reasoning',{}).get('why_enter',''))}")
