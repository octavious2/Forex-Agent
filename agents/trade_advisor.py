"""
Trade Advisor Agent — powered by Llama 3.3 70B via Groq.
Synthesises all agent outputs into a final trade recommendation.
"""
import json
from groq import Groq
from config.settings import GROQ_API_KEY, LLAMA_MODEL, LLAMA_SMALL, MIN_CONFIDENCE, MIN_RR
from database.signal_log import get_performance_summary
from agents.memory import get_recent_narrative, get_pair_context, get_performance_context

client = Groq(api_key=GROQ_API_KEY)

def advise(pair: str, tech: dict, ict: dict,
           session: str, account_balance: float = None) -> dict:
    # Pull real account balance if not explicitly provided
    if account_balance is None:
        try:
            from agents.risk_manager import get_account_balance
            account_balance = get_account_balance()
        except:
            account_balance = 10.0
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
  "setup_type": "order_block / fvg / liquidity_sweep",
  "session_quality": "excellent / good / fair / poor",
  "reasoning": {{
    "why_enter": "2-3 sentence explanation of why this setup is valid",
    "key_confluence": ["list", "of", "confirming", "factors"],
    "main_risk": "biggest risk to this trade",
    "invalidation": "what price level invalidates this setup"
  }},
  "risk_note": "any warnings — high impact news, spread, session"
}}

CONFIDENCE SCORING — be honest and specific, do NOT default to 80:
- 90-100: textbook setup, 4H + 1H + ICT all align, clean structure, ideal entry location
- 75-89:  strong setup, most factors align, only minor caveats
- 60-74:  moderate, mixed signals or some timeframe conflict
- 40-59:  weak, conflicting signals or poor location
- 0-39:   no real setup, stay out
Most genuine setups fall in 55-78. A score of 85+ should be rare and earned.
Vary your score based on how many factors truly align — avoid round defaults.

Rules:
- setup_type MUST be one of: order_block, fvg, liquidity_sweep ONLY
- NEVER use structure_break (11% historical win rate) or confluence (unproven)
- If the only available setup is a structure break, return WAIT
- WAIT if confidence < {MIN_CONFIDENCE} or RR < {MIN_RR}
- WAIT if 4H and 1H trends conflict with ICT bias
- WAIT if in premium zone for BUYs or discount zone for SELLs
- Be specific with prices — no vague zones
- Stop loss must be beyond a structural level, not arbitrary"""

    try:
        try:
            response = client.chat.completions.create(
                model=LLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500,
            )
        except Exception as rate_err:
            if "429" in str(rate_err) or "rate_limit" in str(rate_err).lower():
                print(f"[trade_advisor] 70B rate limited — falling back to 8B")
                response = client.chat.completions.create(
                    model=LLAMA_SMALL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=1500,
                )
            else:
                raise

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

        # Validate TP1 is far enough from entry — reject signals where TP1 is too close
        # Minimum distance prevents broker rejection (error 4756) and unprofitable trades
        try:
            entry_chk = float(result.get("entry_low") or result.get("entry_high") or result.get("price") or 0)
            tp1_chk   = float(result.get("tp1") or 0)
            pip_sz    = 0.0001
            if "JPY" in pair: pip_sz = 0.01
            elif pair == "XAUUSD": pip_sz = 0.1
            elif pair == "BTCUSD": pip_sz = 1.0
            min_tp_pips = 10 if pair not in ("XAUUSD","BTCUSD") else 20
            if entry_chk and tp1_chk:
                tp1_dist = abs(tp1_chk - entry_chk) / pip_sz
                if tp1_dist < min_tp_pips:
                    print(f"[trade_advisor] {pair} TP1 only {tp1_dist:.1f} pips from entry "
                          f"(need {min_tp_pips}) — converting to WAIT")
                    result["decision"] = "WAIT"
                    result["reasoning"] = result.get("reasoning", {})
        except Exception:
            pass


        # Ensure TP2 and TP3 are populated — extrapolate from TP1 and SL if null
        try:
            sl   = float(result.get("stop_loss") or 0)
            tp1  = float(result.get("tp1") or 0)
            entry = float(result.get("entry_high") or result.get("entry_low") or result.get("price") or 0)
            if entry and tp1 and sl:
                risk = abs(entry - sl)
                direction = result.get("decision", "")
                if not result.get("tp2"):
                    if direction == "BUY":
                        result["tp2"] = round(entry + risk * 3.0, 5)
                    else:
                        result["tp2"] = round(entry - risk * 3.0, 5)
                if not result.get("tp3"):
                    if direction == "BUY":
                        result["tp3"] = round(entry + risk * 4.5, 5)
                    else:
                        result["tp3"] = round(entry - risk * 4.5, 5)
        except Exception:
            pass

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

# This prompt addition goes into the trade advisor context
STOP_HUNT_REMINDER = """
CRITICAL: When placing stop loss levels, do NOT place them AT the obvious
structural level. Place them 3-5 pips BEYOND it for forex pairs, 15-20 pips
for Gold. Example: if support is at 1.1640, set SL at 1.1634, not 1.1640.
Institutions specifically target stops placed at round numbers and obvious
technical levels. A stop at 1.1640 will be hunted. A stop at 1.1634 will not.
"""


def deep_verify(pair, signal, tech, ict):
    """
    Skeptical second-opinion pass before committing a trade.
    Hard gates first (non-negotiable), then an AI risk-manager review
    that actively looks for reasons NOT to trade.
    Returns (approved: bool, reason: str).
    """
    # ---- HARD GATES (no AI, cannot be overridden) ----
    try:
        conf = int(float(signal.get("confidence", 0) or 0))
    except (ValueError, TypeError):
        conf = 0
    try:
        rr = float(signal.get("rr_ratio", 0) or 0)
    except (ValueError, TypeError):
        rr = 0.0
    entry = signal.get("entry_low") or signal.get("entry_high")
    sl    = signal.get("stop_loss")
    tp1   = signal.get("tp1")
    decision = signal.get("decision", "WAIT")

    if decision not in ("BUY", "SELL"):
        return False, "not a directional signal"
    if conf < 70:
        return False, f"confidence {conf} below 70"
    if rr < 1.5:
        return False, f"RR {rr} below 1.5"
    if not (entry and sl and tp1):
        return False, "missing entry/SL/TP1"
    if signal.get("setup_type") in ("structure_break", "confluence"):
        return False, f"weak setup type {signal.get('setup_type')}"

    # Compute REAL pip distances so the reviewer judges on numbers, not guesses
    _pip = 0.0001
    if "JPY" in pair: _pip = 0.01
    elif pair == "XAUUSD": _pip = 0.1
    elif pair == "BTCUSD": _pip = 1.0
    try:
        pips_to_sl  = abs(float(entry) - float(sl)) / _pip
        pips_to_tp1 = abs(float(tp1) - float(entry)) / _pip
    except (ValueError, TypeError):
        pips_to_sl = pips_to_tp1 = 0

    # ---- AI SKEPTICAL REVIEW ----
    prompt = f"""You are a skeptical senior risk manager reviewing a proposed trade.
Be critical but FAIR — base concerns on the actual numbers, not assumptions.
Proposed: {decision} {pair}
Entry: {entry}  Stop: {sl}  TP1: {tp1}
Stop is {pips_to_sl:.0f} pips from entry. TP1 is {pips_to_tp1:.0f} pips from entry.
Confidence: {conf}  Risk:Reward: 1:{rr}
Setup type: {signal.get('setup_type')}
4H trend: {tech.get('trend_4h')}  1H trend: {tech.get('trend_1h')}
ICT bias: {ict.get('ict_bias')}
Reasoning given: {json.dumps(signal.get('reasoning', {}))[:500]}

The stop is {pips_to_sl:.0f} pips away — do NOT say "too close to stop" unless under 8 pips.
A 15+ pip stop is normal. Judge ONLY on real flaws: counter-trend entry (fights 4H trend),
chasing price far from value, or genuinely weak/vague structure. If sound, APPROVE.

Respond ONLY with JSON: {{"verdict": "approve" or "reject", "concern": "one short sentence"}}"""

    try:
        raw = None
        # Smart reviewer first: Gemini 2.5 Flash (separate quota, strong reasoning)
        try:
            raw = _gemini_verify(prompt)
            print("  [deep_verify] reviewed by Gemini 2.5 Flash")
        except Exception as gem_err:
            print(f"  [deep_verify] Gemini unavailable ({str(gem_err)[:50]}) — falling back to Llama")
            try:
                response = client.chat.completions.create(
                    model=LLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2, max_tokens=200,
                )
            except Exception as rate_err:
                response = client.chat.completions.create(
                    model=LLAMA_SMALL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2, max_tokens=200,
                )
            raw = response.choices[0].message.content
        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        # Robust JSON extraction
        if not raw.startswith("{"):
            s = raw.find("{")
            e = raw.rfind("}")
            if s >= 0 and e > s:
                raw = raw[s:e+1]
        verdict = json.loads(raw)
        if verdict.get("verdict") == "approve":
            return True, "passed deep verification"
        else:
            return False, "AI veto: " + verdict.get("concern", "setup not strong enough")
    except Exception as e:
        # If the review fails to parse, fall back to hard gates only (already passed)
        return True, "verified on hard gates (AI review unparseable)"
