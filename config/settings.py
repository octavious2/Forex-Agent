import os
from dotenv import load_dotenv
load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────
TWELVE_DATA_KEY   = os.getenv("TWELVE_DATA_KEY")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")

# ── AI Models ─────────────────────────────────────────────────────────
# Scanner, Technical, Fundamental → Gemini 2.5 Flash (high limits, fast)
GEMINI_MODEL      = "gemini-2.5-flash"

# ICT Concepts → DeepSeek-R1 via Groq (deep reasoning)
DEEPSEEK_MODEL    = "qwen/qwen3-32b"

# Trade Advisor → Llama 3.3 70B via Groq (fast synthesis)
LLAMA_MODEL       = "llama-3.3-70b-versatile"

# ── Pairs ─────────────────────────────────────────────────────────────
PAIRS = [
    "EURUSD",  # cleanest structure
    "GBPUSD",  # best for ICT
    "USDJPY",  # risk/yield dynamics
    "XAUUSD",  # gold — best RR
    "GBPJPY",  # high volatility
    "AUDUSD",  # commodity-linked
]

# ── Timeframes ────────────────────────────────────────────────────────
TF_TREND  = "4h"    # higher timeframe bias
TF_ENTRY  = "1h"    # structure and setup
TF_CONFIRM= "15min" # trigger confirmation

# ── Risk ──────────────────────────────────────────────────────────────
MAX_RISK_PCT   = 1.0   # % per trade
MIN_RR         = 1.5   # minimum risk:reward
MIN_CONFIDENCE = 65    # minimum score to send alert

# ── Scanner ───────────────────────────────────────────────────────────
SCAN_INTERVAL_MIN = 15  # run every 15 minutes

# ── Sessions (UTC hours) ──────────────────────────────────────────────
SESSIONS = {
    "sydney":   (22, 7),
    "tokyo":    (0,  9),
    "london":   (7,  16),
    "new_york": (12, 21),
}

# Fast small model for low-criticality tasks (fundamental, memory journal)
# Uses ~80% fewer tokens than Llama 70B
LLAMA_SMALL = "llama-3.1-8b-instant"
