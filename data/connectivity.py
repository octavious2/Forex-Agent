"""
Connectivity checker — tests internet before each scan.
If offline, the agent waits and retries instead of crashing.
"""
import requests
import time

def is_online(timeout: int = 5) -> bool:
    """Check if internet is available."""
    try:
        requests.get("https://api.twelvedata.com", timeout=timeout)
        return True
    except:
        try:
            requests.get("https://api.groq.com", timeout=timeout)
            return True
        except:
            return False

def wait_for_connection(max_wait_minutes: int = 30) -> bool:
    """
    Wait for internet connection to return.
    Returns True when connected, False if max wait exceeded.
    """
    print(f"\n📡 No internet connection — waiting up to {max_wait_minutes} minutes...")
    for attempt in range(max_wait_minutes):
        time.sleep(60)
        if is_online():
            print(f"✅ Internet restored after {attempt+1} minutes — resuming")
            return True
        print(f"  Still offline... ({attempt+1}/{max_wait_minutes} min)")
    return False

def notify_offline():
    try:
        from notifications.discord import send_status_update
        send_status_update(
            "📡 **Agent offline** — no internet connection\n"
            "MT5 EA is managing open positions independently.\n"
            "New signals paused until connection restored.",
            color=0xFF6600
        )
    except:
        pass

def notify_online():
    try:
        from notifications.discord import send_status_update
        send_status_update(
            "✅ **Agent back online** — resuming market analysis",
            color=0x00FF88
        )
    except:
        pass
