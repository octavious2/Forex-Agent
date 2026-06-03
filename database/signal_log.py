import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "signals.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pair          TEXT NOT NULL,
            direction     TEXT NOT NULL,
            entry_low     REAL,
            entry_high    REAL,
            stop_loss     REAL,
            tp1           REAL,
            tp2           REAL,
            tp3           REAL,
            confidence    INTEGER,
            rr_ratio      REAL,
            session       TEXT,
            setup_type    TEXT,
            analysis      TEXT,
            created_at    TEXT NOT NULL,
            outcome       TEXT DEFAULT 'PENDING',
            outcome_tp    INTEGER DEFAULT 0,
            closed_at     TEXT,
            pips_result   REAL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS performance (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pair          TEXT,
            session       TEXT,
            setup_type    TEXT,
            total_signals INTEGER DEFAULT 0,
            wins          INTEGER DEFAULT 0,
            losses        INTEGER DEFAULT 0,
            win_rate      REAL DEFAULT 0,
            avg_pips      REAL DEFAULT 0,
            updated_at    TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("Database initialised")

def log_signal(signal: dict) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO signals
        (pair, direction, entry_low, entry_high, stop_loss,
         tp1, tp2, tp3, confidence, rr_ratio, session,
         setup_type, analysis, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        signal.get("pair"),
        signal.get("direction"),
        signal.get("entry_low"),
        signal.get("entry_high"),
        signal.get("stop_loss"),
        signal.get("tp1"),
        signal.get("tp2"),
        signal.get("tp3"),
        signal.get("confidence"),
        signal.get("rr_ratio"),
        signal.get("session"),
        signal.get("setup_type"),
        signal.get("analysis"),
        datetime.utcnow().isoformat()
    ))
    signal_id = c.lastrowid
    conn.commit()
    conn.close()
    return signal_id

def get_performance_summary(pair: str = None) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if pair:
        c.execute("""
            SELECT pair, direction, outcome, pips_result, setup_type, session
            FROM signals
            WHERE pair=? AND outcome != 'PENDING'
            ORDER BY created_at DESC LIMIT 100
        """, (pair,))
    else:
        c.execute("""
            SELECT pair, direction, outcome, pips_result, setup_type, session
            FROM signals
            WHERE outcome != 'PENDING'
            ORDER BY created_at DESC LIMIT 200
        """)
    rows = c.fetchall()
    conn.close()

    summary = {}
    for row in rows:
        p = row[0]
        if p not in summary:
            summary[p] = {"total": 0, "wins": 0, "losses": 0, "pips": 0}
        summary[p]["total"] += 1
        if row[2] == "WIN":
            summary[p]["wins"] += 1
        elif row[2] == "LOSS":
            summary[p]["losses"] += 1
        summary[p]["pips"] += row[3] or 0

    result = []
    for p, s in summary.items():
        wr = (s["wins"] / s["total"] * 100) if s["total"] > 0 else 0
        result.append({
            "pair": p,
            "total": s["total"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": round(wr, 1),
            "total_pips": round(s["pips"], 1)
        })
    return result

def update_outcome(signal_id: int, outcome: str, tp_hit: int, pips: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE signals
        SET outcome=?, outcome_tp=?, pips_result=?, closed_at=?
        WHERE id=?
    """, (outcome, tp_hit, pips, datetime.utcnow().isoformat(), signal_id))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()

def has_active_signal(pair: str, hours: int = 4) -> bool:
    """Check if pair already has a PENDING signal within the last N hours."""
    import sqlite3
    from datetime import datetime, timezone, timedelta
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c.execute("""
        SELECT COUNT(*) FROM signals
        WHERE pair=? AND outcome='PENDING' AND created_at > ?
    """, (pair, cutoff))
    count = c.fetchone()[0]
    conn.close()
    return count > 0

def get_pending_signals() -> list:
    """Get all signals still marked PENDING."""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        SELECT id, pair, direction, entry_high, entry_low, stop_loss,
               tp1, tp2, tp3, created_at, mt5_ticket
        FROM signals WHERE outcome='PENDING'
        ORDER BY created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        best_entry = r[3] or r[4] or 0
        result.append({
            "id": r[0], "pair": r[1], "direction": r[2],
            "entry": best_entry, "entry_high": r[3], "entry_low": r[4],
            "sl": r[5], "tp1": r[6], "tp2": r[7], "tp3": r[8],
            "created_at": r[9], "mt5_ticket": r[10] or 0
        })
    return result
