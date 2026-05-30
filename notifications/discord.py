"""
Discord notification sender.
Uses the webhook URL already in .env — no bot setup needed.
"""
import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def send_signal(signal: dict):
    """Send a trade signal alert to Discord."""
    if not WEBHOOK_URL:
        print("[discord] No webhook URL configured")
        return

    decision   = signal.get("decision", "WAIT")
    pair       = signal.get("pair", "")
    price      = signal.get("price", 0)
    confidence = signal.get("confidence", 0)
    rr         = signal.get("rr_ratio", 0)

    if decision == "WAIT":
        return  # Don't spam Discord with WAIT signals

    emoji = "🟢" if decision == "BUY" else "🔴"
    session = signal.get("session", "")

    reasoning  = signal.get("reasoning", {})
    confluence = reasoning.get("key_confluence", [])
    conf_text  = "\n".join([f"• {c}" for c in confluence[:4]])

    embed = {
        "title": f"{emoji} {decision} {pair}  |  Confidence: {confidence}/100",
        "color": 0x00FF88 if decision == "BUY" else 0xFF4444,
        "fields": [
            {
                "name": "📍 Entry Zone",
                "value": f"`{signal.get('entry_low') or signal.get('entry_high') or 'Market'} — {signal.get('entry_high') or signal.get('entry_low') or 'Order'}`",
                "inline": True
            },
            {
                "name": "🛑 Stop Loss",
                "value": f"`{signal.get('stop_loss', '')}` ({signal.get('pips_to_sl', '')} pips)",
                "inline": True
            },
            {
                "name": "⚡ Session",
                "value": f"`{session.upper()}`",
                "inline": True
            },
            {
                "name": "🎯 Take Profits",
                "value": (
                    f"TP1: `{signal.get('tp1', '')}` ({signal.get('pips_to_tp1', '')} pips)\n"
                    f"TP2: `{signal.get('tp2', '')}`\n"
                    f"TP3: `{signal.get('tp3', '')}`"
                ),
                "inline": True
            },
            {
                "name": "📊 Risk:Reward",
                "value": f"`1 : {rr}`",
                "inline": True
            },
            {
                "name": "🔍 Setup Type",
                "value": f"`{signal.get('setup_type', '').upper()}`",
                "inline": True
            },
            {
                "name": "🧠 Why Enter",
                "value": reasoning.get("why_enter", "N/A")[:400],
                "inline": False
            },
            {
                "name": "✅ Key Confluence",
                "value": conf_text or "N/A",
                "inline": False
            },
            {
                "name": "⚠️ Main Risk",
                "value": reasoning.get("main_risk", "N/A"),
                "inline": True
            },
            {
                "name": "❌ Invalidation",
                "value": reasoning.get("invalidation", "N/A"),
                "inline": True
            },
        ],
        "footer": {
            "text": f"LifeTap Forex Agent • {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} • YOU make the final call"
        },
        "timestamp": datetime.utcnow().isoformat()
    }

    if signal.get("risk_note"):
        embed["fields"].append({
            "name": "📰 Risk Note",
            "value": signal.get("risk_note"),
            "inline": False
        })

    payload = {"embeds": [embed]}

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code == 204:
            print(f"[discord] ✅ Signal sent: {decision} {pair}")
        else:
            print(f"[discord] ❌ Failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[discord] Error: {e}")

def send_heartbeat(pairs_scanned: int, signals_sent: int):
    """Send a daily summary ping."""
    if not WEBHOOK_URL:
        return
    payload = {
        "embeds": [{
            "title": "💓 Forex Agent — Heartbeat",
            "color": 0x5865F2,
            "fields": [
                {"name": "Pairs Scanned", "value": str(pairs_scanned), "inline": True},
                {"name": "Signals Sent", "value": str(signals_sent), "inline": True},
            ],
            "footer": {"text": datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
        }]
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
    except:
        pass

if __name__ == "__main__":
    print("Testing Discord webhook...")
    test_signal = {
        "pair":       "GBPUSD",
        "decision":   "BUY",
        "price":      1.34422,
        "confidence": 78,
        "rr_ratio":   2.1,
        "entry_low":  1.34380,
        "entry_high": 1.34420,
        "stop_loss":  1.34280,
        "tp1":        1.34580,
        "tp2":        1.34720,
        "tp3":        1.34900,
        "pips_to_sl": 14,
        "pips_to_tp1": 16,
        "setup_type": "order_block",
        "session":    "london",
        "reasoning": {
            "why_enter": "Price tapped into the 1H bullish order block at 1.3438 with a bullish engulfing candle. ICT structure shows higher highs and higher lows intact.",
            "key_confluence": [
                "4H and 1H trend both bullish",
                "Order block holding as support",
                "RSI at 57 — room to run",
                "London kill zone active"
            ],
            "main_risk":   "Resistance cluster at 1.3448 could stall momentum",
            "invalidation": "Close below 1.3428 invalidates the order block"
        },
        "risk_note": "No high-impact GBP news for next 4 hours ✅"
    }
    send_signal(test_signal)

def send_status_update(message: str, color: int = 0x5865F2):
    """Send a simple status message to Discord."""
    import requests as _req
    import os as _os
    webhook = _os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        return
    try:
        _req.post(webhook, json={
            "embeds": [{
                "description": message,
                "color": color,
                "footer": {"text": f"Forex Agent • {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%H:%M UTC')}"}
            }]
        }, timeout=5)
    except:
        pass
