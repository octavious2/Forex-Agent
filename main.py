import sys, signal
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from database.signal_log import init_db
from agents.memory import init_memory_db
from agents.orchestrator import run_once, daily_heartbeat
from agents.outcome_tracker import check_outcomes, expire_old_signals
from agents.bias_detector import report_bias as weekly_bias_report
from agents.signal_monitor import monitor_all
from config.settings import SCAN_INTERVAL_MIN

def handle_shutdown(sig, frame):
    print("\n🛑 Forex Agent shutting down...")
    sys.exit(0)

signal.signal(signal.SIGINT,  handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

if __name__ == "__main__":
    print("="*50)
    print("  LifeTap Forex Agent v1.0")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Scan: every {SCAN_INTERVAL_MIN}min | Monitor: every 5min")
    print("="*50)

    init_db()
    init_memory_db()

    check_outcomes()  # resolve stale pendings from while bot was off
    print("\n🚀 Running initial scan...")
    run_once()

    scheduler = BlockingScheduler(timezone="UTC")

    # Main market scan — every 15 minutes
    scheduler.add_job(
        run_once,
        trigger=IntervalTrigger(minutes=SCAN_INTERVAL_MIN),
        id="market_scan",
        max_instances=1,
        misfire_grace_time=60,
    )

    # Signal monitor — every 5 minutes
    scheduler.add_job(
        monitor_all,
        trigger=IntervalTrigger(minutes=5),
        id="signal_monitor",
        max_instances=1,
        misfire_grace_time=30,
    )

    # Outcome tracker — every 30 minutes
    scheduler.add_job(
        check_outcomes,
        trigger=IntervalTrigger(minutes=30),
        id="outcome_tracker",
        max_instances=1,
    )

    # Weekly bias report — every Sunday 20:00 UTC
    scheduler.add_job(
        weekly_bias_report,
        trigger=CronTrigger(hour=20, minute=0, day_of_week="sun"),
        id="bias_report",
    )

    # Daily heartbeat — 08:00 UTC
    scheduler.add_job(
        daily_heartbeat,
        trigger=CronTrigger(hour=8, minute=0),
        id="heartbeat",
    )

    print(f"\n⏰ Scheduler active:")
    print(f"   Market scan   → every {SCAN_INTERVAL_MIN} minutes")
    print(f"   Signal monitor → every 5 minutes")
    print(f"   Outcome check  → every 30 minutes")
    print(f"   Heartbeat      → daily 08:00 UTC")
    print("\nPress Ctrl+C to stop\n")

    try:
        scheduler.start()
    except Exception as e:
        print(f"Scheduler error: {e}")

def weekly_bias_report():
    """Every Sunday send a bias report to Discord."""
    from agents.bias_detector import report_bias
    report_bias()
