"""
MT5 File Bridge — Python side.
Writes trade signals to a JSON file that the LifeTap EA reads.
Reads back execution results from MT5.

File locations (Linux paths):
  Signal:  ~/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files/lifetap_signal.json
  Result:  ~/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files/lifetap_result.json
  Status:  ~/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files/lifetap_status.json
"""
import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path

MT5_FILES = Path.home() / ".mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
SIGNAL_FILE = MT5_FILES / "lifetap_signal.json"
RESULT_FILE  = MT5_FILES / "lifetap_result.json"
STATUS_FILE  = MT5_FILES / "lifetap_status.json"

# MT5 symbol names (some brokers add suffixes like EURUSD.a)
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "GBPJPY": "GBPJPY",
    "AUDUSD": "AUDUSD",
    "XAUUSD": "XAUUSD",
}

def send_trade(signal: dict, lot_size: float = 0.01) -> dict:
    """
    Write a trade signal for the MT5 EA to execute.
    Returns the execution result or error.
    """
    pair      = signal.get("pair", "")
    direction = signal.get("decision", signal.get("direction", ""))
    entry     = float(signal.get("entry_high") or signal.get("entry_low") or signal.get("price") or 0)
    sl        = float(signal.get("stop_loss") or 0)
    tp1       = float(signal.get("tp1") or 0)
    tp2       = float(signal.get("tp2") or 0)
    tp3       = float(signal.get("tp3") or 0)
    symbol    = SYMBOL_MAP.get(pair, pair)

    if not entry or not sl or not tp1:
        return {"status": "error", "message": "Missing entry, SL or TP"}

    if direction not in ["BUY", "SELL"]:
        return {"status": "error", "message": f"Invalid direction: {direction}"}

    # Clear any old result file
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()

    # Write signal for EA to pick up
    payload = {
        "id":        signal.get("id", 0),
        "symbol":    symbol,
        "action":    direction,
        "lot":       lot_size,
        "price":     entry,
        "sl":        sl,
        "tp1":       tp1,
        "tp2":       tp2,
        "tp3":       tp3,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "executed":  False
    }

    try:
        with open(SIGNAL_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[mt5_bridge] Signal written: {direction} {symbol} lot={lot_size}")
    except Exception as e:
        return {"status": "error", "message": f"Cannot write signal file: {e}"}

    # Wait for EA to execute (up to 30 seconds)
    for i in range(30):
        time.sleep(1)
        if RESULT_FILE.exists():
            try:
                with open(RESULT_FILE) as f:
                    result = json.load(f)
                print(f"[mt5_bridge] Result: {result}")
                return result
            except:
                continue

    return {"status": "timeout", "message": "EA did not respond within 30 seconds"}

def get_open_positions() -> list:
    """Read current open positions from MT5 status file."""
    try:
        if STATUS_FILE.exists():
            with open(STATUS_FILE) as f:
                data = json.load(f)
            return data.get("positions", [])
    except:
        pass
    return []

def close_position(ticket: int) -> dict:
    """Request MT5 EA to close a specific position."""
    payload = {
        "action":    "CLOSE",
        "ticket":    ticket,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()
    with open(SIGNAL_FILE, "w") as f:
        json.dump(payload, f)

    for i in range(15):
        time.sleep(1)
        if RESULT_FILE.exists():
            with open(RESULT_FILE) as f:
                return json.load(f)
    return {"status": "timeout"}

def check_mt5_running() -> bool:
    """Check if MT5 EA is active by looking at status file age."""
    try:
        if STATUS_FILE.exists():
            age = time.time() - STATUS_FILE.stat().st_mtime
            return age < 60  # Updated within last 60 seconds = EA is running
        return False
    except:
        return False

if __name__ == "__main__":
    print(f"MT5 Files path: {MT5_FILES}")
    print(f"Path exists: {MT5_FILES.exists()}")
    print(f"MT5 EA running: {check_mt5_running()}")
    positions = get_open_positions()
    print(f"Open positions: {len(positions)}")
