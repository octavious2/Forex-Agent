"""
MT5 File Bridge — Python side.
Writes trade signals to JSON for LifeTapEA to execute.
Places LIMIT orders at the identified entry zone.
Falls back to MARKET if price is already inside the zone.
"""
import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path

MT5_FILES   = Path.home() / ".mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
SIGNAL_FILE = MT5_FILES / "lifetap_signal.json"
RESULT_FILE = MT5_FILES / "lifetap_result.json"
STATUS_FILE = MT5_FILES / "lifetap_status.json"

SYMBOL_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY", "GBPJPY": "GBPJPY",
    "AUDUSD": "AUDUSD", "XAUUSD": "XAUUSD",
    "BTCUSD": "BTCUSD",
}

def send_trade(signal: dict, lot_size: float = 0.01) -> dict:
    pair      = signal.get("pair", "")
    direction = signal.get("decision", signal.get("direction", ""))
    entry_low  = float(signal.get("entry_low")  or signal.get("price") or 0)
    entry_high = float(signal.get("entry_high") or signal.get("price") or 0)
    sl         = float(signal.get("stop_loss") or 0)
    tp1        = float(signal.get("tp1") or 0)
    tp2        = float(signal.get("tp2") or 0)
    tp3        = float(signal.get("tp3") or 0)
    symbol     = SYMBOL_MAP.get(pair, pair)

    if not sl or not tp1:
        return {"status": "error", "message": "Missing SL or TP"}
    if direction not in ["BUY", "SELL"]:
        return {"status": "error", "message": f"Invalid direction: {direction}"}

    # Determine limit price
    # BUY LIMIT: place at entry_low (buy when price pulls back to zone bottom)
    # SELL LIMIT: place at entry_high (sell when price rallies to zone top)
    if direction == "BUY":
        limit_price = entry_low if entry_low > 0 else entry_high
    else:
        limit_price = entry_high if entry_high > 0 else entry_low

    if RESULT_FILE.exists():
        RESULT_FILE.unlink()

    payload = {
        "id":           signal.get("id", 0),
        "symbol":       symbol,
        "action":       direction,
        "order_type":   "LIMIT",      # always place limit order
        "lot":          lot_size,
        "limit_price":  limit_price,
        "entry_low":    entry_low,
        "entry_high":   entry_high,
        "sl":           sl,
        "tp1":          tp1,
        "tp2":          tp2,
        "tp3":          tp3,
        "expiry_hours": 8,            # cancel if not filled within 8 hours
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "executed":     False
    }

    try:
        with open(SIGNAL_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[mt5_bridge] Limit order written: {direction} LIMIT {symbol} "
              f"@ {limit_price} lot={lot_size}")
    except Exception as e:
        return {"status": "error", "message": f"Cannot write signal: {e}"}

    # Wait for EA response (up to 30 seconds)
    for _ in range(30):
        time.sleep(1)
        if RESULT_FILE.exists():
            try:
                with open(RESULT_FILE) as f:
                    result = json.load(f)
                print(f"[mt5_bridge] Result: {result}")
                return result
            except:
                continue

    return {"status": "timeout", "message": "EA did not respond in 30s"}

def get_open_positions() -> list:
    try:
        if STATUS_FILE.exists():
            with open(STATUS_FILE) as f:
                return json.load(f).get("positions", [])
    except:
        pass
    return []

def close_position(ticket: int) -> dict:
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()
    with open(SIGNAL_FILE, "w") as f:
        json.dump({"action": "CLOSE", "ticket": ticket,
                   "timestamp": datetime.now(timezone.utc).isoformat()}, f)
    for _ in range(15):
        time.sleep(1)
        if RESULT_FILE.exists():
            with open(RESULT_FILE) as f:
                return json.load(f)
    return {"status": "timeout"}

def check_mt5_running() -> bool:
    try:
        if STATUS_FILE.exists():
            return (time.time() - STATUS_FILE.stat().st_mtime) < 60
        return False
    except:
        return False

if __name__ == "__main__":
    print(f"MT5 Files: {MT5_FILES}")
    print(f"Path exists: {MT5_FILES.exists()}")
    print(f"EA running: {check_mt5_running()}")
