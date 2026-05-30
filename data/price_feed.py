import requests
import pandas as pd
from datetime import datetime, timezone
import os
import time
from dotenv import load_dotenv

load_dotenv()
TWELVE_KEY = os.getenv("TWELVE_DATA_KEY")
BASE_URL   = "https://api.twelvedata.com"

VALID_PAIRS = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "XAUUSD": "XAU/USD",
    "BTCUSD": "BTC/USD",
}

# Simple in-memory cache — stores candles per (pair, timeframe)
# Refreshed every 14 minutes so we don't re-fetch within same scan cycle
_cache     = {}
_cache_ttl = 840  # 14 minutes in seconds

def _cache_key(pair, tf):
    return f"{pair}_{tf}"

def get_candles(pair: str, timeframe: str, bars: int = 100) -> pd.DataFrame:
    symbol = VALID_PAIRS.get(pair)
    if not symbol:
        return pd.DataFrame()

    key    = _cache_key(pair, timeframe)
    now    = time.time()

    # Return cached data if fresh enough
    if key in _cache:
        cached_time, cached_df = _cache[key]
        if now - cached_time < _cache_ttl:
            return cached_df

    params = {
        "symbol":     symbol,
        "interval":   timeframe,
        "outputsize": bars,
        "apikey":     TWELVE_KEY,
        "format":     "JSON",
    }

    try:
        # Polite delay to stay under rate limit
        time.sleep(8)

        r    = requests.get(f"{BASE_URL}/time_series", params=params, timeout=20)
        data = r.json()

        if data.get("status") == "error":
            msg = data.get("message", "")
            if "out of API credits" in msg:
                print(f"[price_feed] Rate limit hit — waiting 60s...")
                time.sleep(60)
                r    = requests.get(f"{BASE_URL}/time_series", params=params, timeout=20)
                data = r.json()
            else:
                print(f"[price_feed] API error {pair} {timeframe}: {msg}")
                return pd.DataFrame()

        values = data.get("values", [])
        if not values:
            return pd.DataFrame()

        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        cols = [c for c in ["open","high","low","close"] if c in df.columns]
        df = df[cols].astype(float)

        # Store in cache
        _cache[key] = (now, df)
        return df

    except Exception as e:
        print(f"[price_feed] Error {pair} {timeframe}: {e}")
        return pd.DataFrame()

def prefetch_pair(pair: str):
    """Fetch all timeframes for a pair upfront in one batch to save rate limit."""
    print(f"  [data] Fetching {pair}...")
    get_candles(pair, "4h",   100)
    get_candles(pair, "1h",   100)
    get_candles(pair, "15min", 50)

def get_current_price(pair: str) -> float:
    symbol = VALID_PAIRS.get(pair)
    if not symbol:
        return 0.0
    try:
        r = requests.get(f"{BASE_URL}/price",
                         params={"symbol": symbol, "apikey": TWELVE_KEY},
                         timeout=10)
        return float(r.json().get("price", 0))
    except:
        return 0.0

def get_session() -> str:
    hour = datetime.now(timezone.utc).hour
    if 7 <= hour < 12:
        return "london"
    elif 12 <= hour < 16:
        return "london_newyork_overlap"
    elif 16 <= hour < 21:
        return "new_york"
    elif 0 <= hour < 7:
        return "tokyo"
    else:
        return "sydney"

def pip_value(pair: str) -> float:
    if "JPY" in pair:
        return 0.01
    elif pair == "XAUUSD":
        return 0.1
    elif pair == "BTCUSD":
        return 1.0   # $1 per unit for BTC
    else:
        return 0.0001
