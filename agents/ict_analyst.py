"""
ICT Concepts Agent — powered by DeepSeek-R1 via Groq.
Identifies: Order Blocks, Fair Value Gaps, Liquidity Sweeps,
Market Structure Breaks, Kill Zones, Premium/Discount Arrays.
"""
import json
from groq import Groq
from data.price_feed import get_candles, pip_value
from config.settings import GROQ_API_KEY, DEEPSEEK_MODEL

client = Groq(api_key=GROQ_API_KEY)

def analyse(pair: str, tech_data: dict) -> dict:
    """
    Run ICT analysis on a pair using DeepSeek-R1 reasoning.
    tech_data: output from technical.py analyse()
    """
    df_4h  = get_candles(pair, "4h",   20)
    df_1h  = get_candles(pair, "1h",   20)
    df_15m = get_candles(pair, "15min", 10)

    if df_1h.empty:
        return {"pair": pair, "error": "No price data"}

    # Build raw price context for the model
    candles_4h  = _candles_to_text(df_4h.tail(6),  "4H")
    candles_1h  = _candles_to_text(df_1h.tail(8),  "1H")
    candles_15m = _candles_to_text(df_15m.tail(5), "15M")

    price   = tech_data.get("current_price", 0)
    pip     = pip_value(pair)
    struct  = tech_data.get("structure_1h", {})
    levels  = tech_data.get("key_levels", {})

    prompt = f"""You are an expert ICT (Inner Circle Trader) analyst.
Analyse {pair} and identify institutional trading concepts.

Current price: {price}
Pip value: {pip}
1H Structure: {struct}
Key levels: {levels}

Recent price action:
{candles_4h}
{candles_1h}
{candles_15m}

IMPORTANT: Deprioritise order blocks and FVGs that are at round numbers, previous week highs/lows, or levels visible on higher timeframes than 4H. These are the levels institutions know retail traders are watching and they use them to trap entries. Prefer less obvious but technically valid levels.

Identify and respond ONLY with valid JSON (no markdown, no explanation outside JSON):

{{
  "order_blocks": [
    {{
      "type": "bullish or bearish",
      "zone_high": <price>,
      "zone_low": <price>,
      "timeframe": "4H or 1H",
      "strength": "strong/moderate/weak",
      "description": "brief explanation"
    }}
  ],
  "fair_value_gaps": [
    {{
      "type": "bullish or bearish",
      "gap_high": <price>,
      "gap_low": <price>,
      "timeframe": "4H or 1H",
      "filled": true/false
    }}
  ],
  "liquidity_levels": {{
    "buy_side_liquidity": [<price levels above current price>],
    "sell_side_liquidity": [<price levels below current price>],
    "equal_highs": <price or null>,
    "equal_lows": <price or null>
  }},
  "market_structure": {{
    "htf_bias": "bullish or bearish or ranging",
    "last_bos": "bullish or bearish or none",
    "last_choch": "bullish or bearish or none",
    "premium_or_discount": "premium or discount or equilibrium"
  }},
  "kill_zone": {{
    "active": true/false,
    "name": "london_open / ny_open / london_close or none",
    "description": "brief note"
  }},
  "ict_bias": "bullish or bearish or neutral",
  "best_setup": {{
    "direction": "BUY or SELL or WAIT",
    "entry_idea": "brief description of ideal entry",
    "invalidation": "what would invalidate this setup",
    "confidence": <0-100>
  }}
}}"""

    try:
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
        except Exception as rate_err:
            if "429" in str(rate_err) or "rate_limit" in str(rate_err).lower():
                print(f"[ict_analyst] Qwen rate limited — falling back to 8B")
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=1500,
                )
            else:
                raise

        raw = response.choices[0].message.content.strip()

        # DeepSeek-R1 wraps reasoning in <think> tags — strip them
        if "<think>" in raw:
            raw = raw[raw.find("</think>") + 8:].strip()

        # Extract JSON
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        result = json.loads(raw)
        result["pair"] = pair
        return result

    except json.JSONDecodeError as e:
        print(f"[ict_analyst] JSON parse error for {pair}: {e}")
        return {"pair": pair, "error": "JSON parse failed", "raw": raw[:200]}
    except Exception as e:
        print(f"[ict_analyst] Error for {pair}: {e}")
        return {"pair": pair, "error": str(e)}

def _candles_to_text(df, label: str) -> str:
    if df.empty:
        return f"{label}: no data"
    lines = [f"\n{label} candles (oldest→newest):"]
    for ts, row in df.iterrows():
        lines.append(
            f"  {str(ts)[:16]}  "
            f"O:{row['open']:.5f} H:{row['high']:.5f} "
            f"L:{row['low']:.5f}  C:{row['close']:.5f}"
        )
    return "\n".join(lines)

if __name__ == "__main__":
    from agents.technical import analyse as ta_analyse
    print("Testing ICT analyst on EURUSD...")
    tech = ta_analyse("EURUSD")
    result = analyse("EURUSD", tech)
    print(json.dumps(result, indent=2, default=str))
