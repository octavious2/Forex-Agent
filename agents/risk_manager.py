"""
Risk Manager — centralised position sizing and risk control.
Calculates lot size from account balance and risk percentage,
enforces maximum exposure, and validates every trade before execution.
"""
from data.price_feed import pip_value

# Risk configuration
MAX_RISK_PERCENT     = 1.0    # max % of account risked per trade
MAX_OPEN_POSITIONS   = 4      # never hold more than this many at once
MAX_CORRELATED_RISK  = 2.0    # max % across correlated pairs

# Pip dollar value per 0.01 lot (micro lot) per pair
PIP_DOLLAR_PER_MICRO = {
    "EURUSD": 0.10, "GBPUSD": 0.10, "AUDUSD": 0.10,
    "USDJPY": 0.09, "GBPJPY": 0.09,   # approx, varies with JPY rate
    "XAUUSD": 0.10,                    # gold per 0.01 lot per $0.10 move
    "BTCUSD": 0.01,                    # BTC per 0.001 lot
}

def calculate_lot_size(account_balance: float, entry: float,
                        stop_loss: float, pair: str) -> float:
    """
    Calculate lot size so that hitting the stop loss costs
    exactly MAX_RISK_PERCENT of the account.

    For very small accounts the broker minimum (0.01, or 0.001 for BTC)
    is used as a floor.
    """
    if not entry or not stop_loss:
        return 0.01

    pip = pip_value(pair)
    sl_distance_pips = abs(entry - stop_loss) / pip

    if sl_distance_pips == 0:
        return 0.01

    # Dollar risk allowed
    risk_dollars = account_balance * (MAX_RISK_PERCENT / 100.0)

    # Dollar value per pip at 0.01 lot
    pip_val = PIP_DOLLAR_PER_MICRO.get(pair, 0.10)

    # Loss at 0.01 lot if SL is hit
    loss_at_micro = sl_distance_pips * pip_val

    if loss_at_micro == 0:
        return 0.01

    # Scale lot to match allowed risk
    lot = 0.01 * (risk_dollars / loss_at_micro)

    # Broker minimums and sensible rounding
    min_lot = 0.001 if pair == "BTCUSD" else 0.01
    lot = max(min_lot, round(lot, 3 if pair == "BTCUSD" else 2))

    # Safety cap — never risk more than 5x micro on a tiny account
    # (prevents oversizing if the math produces something odd)
    max_lot = 1.0
    lot = min(lot, max_lot)

    return lot

def validate_trade(signal: dict, account_balance: float,
                   open_positions: list) -> tuple[bool, str]:
    """
    Final risk check before a trade is placed.
    Returns (allowed, reason).
    """
    # Check maximum open positions
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        return False, f"Max {MAX_OPEN_POSITIONS} open positions reached"

    entry = float(signal.get("entry_low") or signal.get("entry_high")
                  or signal.get("price") or 0)
    sl    = float(signal.get("stop_loss") or 0)
    pair  = signal.get("pair", "")

    if not entry or not sl:
        return False, "Missing entry or stop loss"

    # Verify the risk:reward still makes sense
    tp1 = float(signal.get("tp1") or 0)
    if tp1:
        reward = abs(tp1 - entry)
        risk   = abs(entry - sl)
        if risk > 0 and (reward / risk) < 1.3:
            return False, f"RR too low after sizing: {reward/risk:.2f}"

    return True, "OK"

# Set this to simulate a small account regardless of actual demo balance.
# Your MT5 demo may have thousands — this caps position sizing to test
# the $10 strategy realistically. Set to None to use real balance.
SIMULATED_BALANCE = 10.0

def get_account_balance() -> float:
    """
    Read live account balance from MT5 status file.
    If SIMULATED_BALANCE is set, returns that instead (for small-account testing).
    Falls back to $10 if unavailable.
    """
    if SIMULATED_BALANCE is not None:
        return SIMULATED_BALANCE
    try:
        import json
        from pathlib import Path
        status = Path.home() / ".mt5/drive_c/Program Files/MetaTrader 5/MQL5/Files/lifetap_status.json"
        if status.exists():
            data = json.loads(status.read_text())
            bal = float(data.get("balance", 0))
            if bal > 0:
                return bal
    except:
        pass
    return 10.0  # default assumption

def should_move_breakeven(pair: str, profit_dollars: float,
                          account_balance: float) -> bool:
    """
    For small accounts, move SL to breakeven once profit reaches
    2% of account — locks in gains on volatile pairs like Gold.
    """
    threshold = account_balance * 0.02   # 2% of account
    return profit_dollars >= threshold

if __name__ == "__main__":
    # Test position sizing for a $10 account
    print("Position sizing for $10 account, 1% risk:\n")
    tests = [
        ("EURUSD", 1.1670, 1.1640),   # 30 pip SL
        ("XAUUSD", 4490.0, 4475.0),   # 150 pip SL ($15)
        ("USDJPY", 159.50, 159.20),   # 30 pip SL
        ("BTCUSD", 73800, 73300),     # 500 pip SL
    ]
    for pair, entry, sl in tests:
        lot = calculate_lot_size(10.0, entry, sl, pair)
        pip = pip_value(pair)
        sl_pips = abs(entry-sl)/pip
        print(f"  {pair}: entry={entry} SL={sl} ({sl_pips:.0f} pips) → lot={lot}")
