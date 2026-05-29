"""
Trade Advisor Agent — powered by Llama 3.3 70B via Groq.
Synthesises all agent outputs into a final trade recommendation.
"""
import json
from groq import Groq
from config.settings import GROQ_API_KEY, LLAMA_MODEL, MIN_CONFIDENCE, MIN_RR
from database.signal_log import get_performance_summary
from agents.memory import get_recent_narrative, get_pair_context, get_performance_context

client = Groq(api_key=GROQ_API_KEY)

def advise(pair: str, tech: dict, ict: dict,
           session: str, account_balance: float = 200.0) -> dict:
    """
    Final synthesis. Returns trade recommendation or WAIT.
    """
    # Pull historical performance for this pair — the learning memory
    history = get_performance_summary(pair)
    perf    = next((x for x in history if x["pair"] == pair), None)
    market_memory = get_recent_narrative(entries=3)
    pair_context  = get_pair_context(pair)
    perf_context  = get_performance_context()
    perf_str = (
        f"Historical performance on {pair}: "
        f"{perf['total']} signals, {perf['win_rate']}% win rate, "
        f"{perf['total_pips']} total pips"
        if perf else f"No historical data for {pair} yet"
    )

    price   = tech.get("current_price", 0)
    ind     = tech.get("indicators", {})
    struct  = tech.get("structure_1h", {})
    levels  = tech.get("key_levels", {})
    ict_bias = ict.get("ict_bias", "neutral")
    best    = ict.get("best_setup", {})
    obs     = ict.get("order_blocks", [])
    fvgs    = ict.get("fair_value_gaps", [])
    liq     = ict.get("liquidity_levels", {})

    prompt = f"""You are a professional forex trade advisor combining ICT methodology
with technical analysis to provide precise, actionable trade guidance.

PAIR: {pair}
CURRENT PRICE: {price}
SESSION: {session}
ACCOUNT BALANCE: ${account_balance}

TECHNICAL DATA:
- 4H Trend: {tech.get('trend_4h')}
- 1H Trend: {tech.get('trend_1h')}
- Market Structure: {struct}
- RSI: {ind.get('rsi')} ({ind.get('rsi_zone')})
- MACD Cross: {ind.get('macd_cross')}
- BB Position: {ind.get('bb_position')} (0=lower band, 1=upper band)
- ATR: {ind.get('atr')}
- Key Levels: {levels}
- Last 1H Candle: {tech.get('candle_1h')}

ICT ANALYSIS:
- HTF Bias: {ict.get('market_structure', {}).get('htf_bias')}
- ICT Bias: {ict_bias}
- BOS/CHoCH: {ict.get('market_structure', {})}
- Premium/Discount: {ict.get('market_structure', {}).get('premium_or_discount')}
- Order Blocks: {json.dumps(obs[:2])}
- Fair Value Gaps: {json.dumps(fvgs[:2])}
- Liquidity: {liq}
- Kill Zone: {ict.get('kill_zone', {})}
- Best ICT Setup: {best}

SYSTEM MEMORY:
{perf_str}

Based on ALL of this data, provide your trade recommendation.
Respond ONLY with valid JSON:

{{
  "decision": "BUY or SELL or WAIT",
  "confidence": <0-100>,
  "entry_low": <price or null>,
  "entry_high": <price or null>,
  "stop_loss": <price>,
  "tp1": <price>,
  "tp2": <price>,
  "tp3": <price>,
  "rr_ratio": <float>,
  "pips_to_sl": <number>,
  "pips_to_tp1": <number>,
  "setup_type": "order_block / fvg / liquidity_sweep / structure_break / confluence",
  "session_quality": "excellent / good / fair / poor",
  "reasoning": {{
    "why_enter": "2-3 sentence explanation of why this setup is valid",
    "key_confluence": ["list", "of", "confirming", "factors"],
    "main_risk": "biggest risk to this trade",
    "invalidation": "what price level invalidates this setup"
  }},
  "risk_note": "any warnings — high impact news, spread, session"
}}

Rules:
- WAIT if confidence < {MIN_CONFIDENCE} or RR < {MIN_RR}
- WAIT if 4H and 1H trends conflict with ICT bias
- WAIT if in premium zone for BUYs or discount zone for SELLs
- Be specific with prices — no vague zones
- Stop loss must be beyond a structural level, not arbitrary"""

    try:
        response = client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content.strip()

        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        result        = json.loads(raw)
        result["pair"]  = pair
        result["price"] = price

        # Ensure entry_low and entry_high are both populated
        if not result.get("entry_low") and result.get("entry_high"):
            pip = 0.0001
            if "JPY" in pair: pip = 0.01
            elif pair == "XAUUSD": pip = 0.1
            result["entry_low"] = round(float(result["entry_high"]) - 5 * pip, 5)
        elif not result.get("entry_high") and result.get("entry_low"):
            pip = 0.0001
            if "JPY" in pair: pip = 0.01
            elif pair == "XAUUSD": pip = 0.1
            result["entry_high"] = round(float(result["entry_low"]) + 5 * pip, 5)

        return result

    except json.JSONDecodeError as e:
        print(f"[trade_advisor] JSON parse error for {pair}: {e}")
        return {"pair": pair, "decision": "WAIT", "error": "parse failed"}
    except Exception as e:
        print(f"[trade_advisor] Error for {pair}: {e}")
        return {"pair": pair, "decision": "WAIT", "error": str(e)}

if __name__ == "__main__":
    from agents.technical import analyse as ta_analyse
    from agents.ict_analyst import analyse as ict_analyse
    from data.price_feed import get_session

    print("Testing Trade Advisor on GBPUSD...")
    tech   = ta_analyse("GBPUSD")
    ict    = ict_analyse("GBPUSD", tech)
    result = advise("GBPUSD", tech, ict, get_session())
    print(json.dumps(result, indent=2, default=str))
