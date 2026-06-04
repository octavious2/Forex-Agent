import os
import json
import pandas as pd
import numpy as np
import ta
from dotenv import load_dotenv
from google import genai
from google.genai import types
from groq import Groq
from data.price_feed import get_candles, pip_value

load_dotenv()

# Initialize API Clients
GOOGLE_KEY = os.getenv("GEMINI_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")

# Initialize the modern Google GenAI Client wrapper if key exists
ai_client = None
if GOOGLE_KEY:
    ai_client = genai.Client(api_key=GOOGLE_KEY)

def analyse(pair: str, fallback_to_groq: bool = False) -> dict:
    """
    Full technical analysis on a pair.
    Calculates technical indicators locally, then leverages LLM reasoning 
    to output structured market trends and scores.
    """
    df_4h  = get_candles(pair, "4h",   100)
    df_1h  = get_candles(pair, "1h",   100)
    df_15m = get_candles(pair, "15min", 50)

    if df_1h.empty:
        return {"pair": pair, "error": "No primary 1H data returned from data feed"}

    raw_analysis = {
        "pair":          pair,
        "pip_value":     pip_value(pair),
        "trend_4h":      _trend(df_4h) if not df_4h.empty else "unknown",
        "trend_1h":      _trend(df_1h),
        "structure_1h":  _market_structure(df_1h),
        "indicators":    _indicators(df_1h),
        "key_levels":    _key_levels(df_1h),
        "candle_1h":     _last_candle(df_1h),
        "candle_15m":    _last_candle(df_15m) if not df_15m.empty else {},
        "current_price": float(df_1h["close"].iloc[-1]),
    }

    ai_prompt = f"""
    You are an institutional Forex Quantitative Analyst. Review these locally calculated indicators for {pair}:
    Current Price: {raw_analysis['current_price']}
    4H Macro Trend Direction: {raw_analysis['trend_4h']}
    1H Micro Trend Direction: {raw_analysis['trend_1h']}
    Market Structure Bias (1H): {raw_analysis['structure_1h'].get('bias')}
    Key Levels Found: Support1={raw_analysis['key_levels'].get('support1')}, Resistance1={raw_analysis['key_levels'].get('resistance1')}
    Momentum Indicators: RSI={raw_analysis['indicators'].get('rsi')} ({raw_analysis['indicators'].get('rsi_zone')}), MACD={raw_analysis['indicators'].get('macd_cross')}
    Candlestick Patterns: 1H Candle Type={raw_analysis['candle_1h'].get('type')}, 15M Candle Type={raw_analysis['candle_15m'].get('type')}

    Synthesize these data vectors into a structured overview. 
    You MUST respond with a valid JSON object matching this exact schema template:
    {{
        "technical_summary_bias": "BULLISH / BEARISH / NEUTRAL",
        "confluence_narrative": "A concise text summary explaining how the indicators intersect with support/resistance levels.",
        "score_contribution": 0-100
    }}
    """

    if True:  # Groq primary always  # Groq primary, Gemini when stable
        ai_synthesis = _query_groq_analyst(ai_prompt)
    else:
        ai_synthesis = _query_gemini_analyst(ai_prompt)

    raw_analysis["ai_evaluation"] = ai_synthesis
    return raw_analysis

def _query_gemini_analyst(prompt: str) -> dict:
    """Queries Gemini 2.5 Flash using the modern SDK client with structured JSON forcing."""
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"[technical_agent] Gemini invocation error: {e}. Attempting automated fallback to Groq module...")
        return _query_groq_analyst(prompt)

def _query_groq_analyst(prompt: str) -> dict:
    """Queries Llama-3.3-70b via Groq API infrastructure with forced JSON object delivery."""
    if not GROQ_KEY:
        print("[technical_agent] Error: No valid Groq key setup discovered in configurations")
        return {"technical_summary_bias": "NEUTRAL", "confluence_narrative": "API keys missing", "score_contribution": 0}
    try:
        client = Groq(api_key=GROQ_KEY)
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a quantitative finance bot. Respond exclusively in structured JSON formats."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            temperature=0.2
        )
        return json.loads(chat_completion.choices[0].message.content)
    except Exception as e:
        print(f"[technical_agent] Critical Error calling Groq processing frame: {e}")
        return {"technical_summary_bias": "NEUTRAL", "confluence_narrative": "LLM pipeline parsing breakdown", "score_contribution": 0}

def _trend(df: pd.DataFrame) -> str:
    """EMA 20/50 trend direction."""
    if len(df) < 51:
        return "unknown"
    ema20 = df["close"].ewm(span=20).mean().iloc[-1]
    ema50 = df["close"].ewm(span=50).mean().iloc[-1]
    price = df["close"].iloc[-1]

    # Separation between EMAs as % — avoids forcing ambiguous cases into a direction
    sep = (ema20 - ema50) / ema50 * 100  # positive = bullish lean, negative = bearish

    if price > ema20 > ema50:
        return "bullish"
    elif price < ema20 < ema50:
        return "bearish"
    elif sep > 0.05:        # EMA20 clearly above EMA50 → genuine bullish pullback
        return "bullish_pullback"
    elif sep < -0.05:       # EMA20 clearly below EMA50 → genuine bearish pullback
        return "bearish_pullback"
    else:
        return "ranging"    # EMAs too close to call — neutral, no directional bias

def _market_structure(df: pd.DataFrame) -> dict:
    """Identify HH/HL (uptrend) or LH/LL (downtrend)."""
    if len(df) < 20:
        return {}

    highs = df["high"].rolling(5).max().dropna()
    lows  = df["low"].rolling(5).min().dropna()

    recent_highs = highs.tail(4).values
    recent_lows  = lows.tail(4).values

    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return {"bias": "ranging"}

    hh = all(recent_highs[i] <= recent_highs[i+1] for i in range(len(recent_highs)-1))
    hl = all(recent_lows[i]  <= recent_lows[i+1] for i in range(len(recent_lows)-1))
    lh = all(recent_highs[i] >= recent_highs[i+1] for i in range(len(recent_highs)-1))
    ll = all(recent_lows[i]  >  recent_lows[i+1] for i in range(len(recent_lows)-1))

    if hh and hl:
        bias = "bullish"
    elif lh and ll:
        bias = "bearish"
    else:
        bias = "ranging"

    return {
        "bias":        bias,
        "last_high":   float(df["high"].tail(20).max()),
        "last_low":    float(df["low"].tail(20).min()),
        "swing_high":  float(df["high"].tail(5).max()),
        "swing_low":   float(df["low"].tail(5).min()),
    }

def _indicators(df: pd.DataFrame) -> dict:
    """RSI, MACD, Bollinger Bands, ATR."""
    if len(df) < 30:
        return {}

    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

    macd_obj  = ta.trend.MACD(close)
    macd_line = macd_obj.macd().iloc[-1]
    macd_sig  = macd_obj.macd_signal().iloc[-1]
    macd_hist = macd_obj.macd_diff().iloc[-1]

    bb     = ta.volatility.BollingerBands(close, window=20)
    bb_up  = bb.bollinger_hband().iloc[-1]
    bb_mid = bb.bollinger_mavg().iloc[-1]
    bb_low = bb.bollinger_lband().iloc[-1]
    price  = close.iloc[-1]
    bb_pos = (price - bb_low) / (bb_up - bb_low) if (bb_up - bb_low) > 0 else 0.5

    atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1]

    ema21  = close.ewm(span=21).mean().iloc[-1]
    ema50  = close.ewm(span=50).mean().iloc[-1]
    ema200 = close.ewm(span=200).mean().iloc[-1] if len(df) >= 200 else None

    return {
        "rsi":        round(float(rsi), 2),
        "rsi_zone":  "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral",
        "macd_line": round(float(macd_line), 6),
        "macd_sig":  round(float(macd_sig), 6),
        "macd_hist": round(float(macd_hist), 6),
        "macd_cross": "bullish" if macd_line > macd_sig else "bearish",
        "bb_upper":  round(float(bb_up), 5),
        "bb_mid":    round(float(bb_mid), 5),
        "bb_lower":  round(float(bb_low), 5),
        "bb_position": round(float(bb_pos), 3),
        "atr":       round(float(atr), 5),
        "ema21":     round(float(ema21), 5),
        "ema50":     round(float(ema50), 5),
        "ema200":    round(float(ema200), 5) if ema200 else None,
    }

def _key_levels(df: pd.DataFrame) -> dict:
    """Support and resistance levels from recent swing highs/lows."""
    if len(df) < 50:
        return {}

    recent = df.tail(50)
    price  = float(df["close"].iloc[-1])

    swing_highs = []
    swing_lows  = []
    for i in range(2, len(recent) - 2):
        h = recent["high"].iloc
        l = recent["low"].iloc
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            swing_highs.append(float(h[i]))
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            swing_lows.append(float(l[i]))

    resistances = sorted([x for x in swing_highs if x > price])
    supports    = sorted([x for x in swing_lows  if x < price], reverse=True)

    return {
        "resistance1": resistances[0] if len(resistances) > 0 else None,
        "resistance2": resistances[1] if len(resistances) > 1 else None,
        "support1":    supports[0]    if len(supports)    > 0 else None,
        "support2":    supports[1]    if len(supports)    > 1 else None,
    }

def _last_candle(df: pd.DataFrame) -> dict:
    """Classify the most recent candle."""
    if df.empty:
        return {}

    c = df.iloc[-1]
    body    = abs(c["close"] - c["open"])
    full    = c["high"] - c["low"]
    upper_w = c["high"] - max(c["close"], c["open"])
    lower_w = min(c["close"], c["open"]) - c["low"]

    body_pct  = body / full if full > 0 else 0
    upper_pct = upper_w / full if full > 0 else 0
    lower_pct = lower_w / full if full > 0 else 0

    bull = c["close"] > c["open"]

    if body_pct > 0.6:
        ctype = "strong_bullish" if bull else "strong_bearish"
    elif lower_pct > 0.5 and body_pct < 0.3:
        ctype = "hammer" if bull else "shooting_star"
    elif upper_pct > 0.5 and body_pct < 0.3:
        ctype = "shooting_star" if not bull else "hammer"
    elif body_pct < 0.1:
        ctype = "doji"
    else:
        ctype = "bullish" if bull else "bearish"

    return {
        "type":      ctype,
        "open":      round(float(c["open"]),  5),
        "high":      round(float(c["high"]),  5),
        "low":       round(float(c["low"]),   5),
        "close":     round(float(c["close"]), 5),
        "bullish":   bool(bull),
        "body_pct":  round(float(body_pct),  3),
    }

if __name__ == "__main__":
    print("Testing Technical Analyst Agent using modern keys architecture...")
    result = analyse("GBPUSD")
    print(json.dumps(result, indent=2))
