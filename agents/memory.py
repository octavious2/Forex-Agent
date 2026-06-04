"""
Market Memory System.
Writes a journal entry after each scan.
Reads recent entries before the next scan.
Agents use this to understand what has been happening.
"""
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()
client   = Groq(api_key=os.getenv("GROQ_API_KEY"))
DB_PATH  = Path(__file__).parent.parent / "signals.db"

def init_memory_db():
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS market_journal (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session     TEXT,
            pairs_data  TEXT,
            narrative   TEXT,
            regime      TEXT,
            created_at  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pair_state (
            pair        TEXT PRIMARY KEY,
            regime      TEXT,
            bias        TEXT,
            key_level   REAL,
            atr         REAL,
            notes       TEXT,
            updated_at  TEXT
        )
    """)
    conn.commit()
    conn.close()

def write_journal(session: str, scan_results: list):
    """
    After a scan completes, write what was observed.
    scan_results: list of dicts with pair analysis summaries.
    """
    if not scan_results:
        return

    # Build summary of what agents saw
    summary_parts = []
    for r in scan_results:
        pair      = r.get("pair", "")
        decision  = r.get("decision", "WAIT")
        conf      = r.get("confidence", 0)
        trend_4h  = r.get("trend_4h", "")
        trend_1h  = r.get("trend_1h", "")
        price     = r.get("price", 0)
        ict_bias  = r.get("ict_bias", "neutral")
        setup     = r.get("setup_type", "")
        rr        = r.get("rr_ratio", 0)
        summary_parts.append(
            f"{pair}: price={price}, 4H={trend_4h}, 1H={trend_1h}, "
            f"ICT={ict_bias}, signal={decision}({conf}%), RR={rr}"
        )

    summary_text = "\n".join(summary_parts)

    # Ask AI to write a concise journal entry
    prompt = f"""You are an experienced forex trader writing your trading journal.

Session: {session}
Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

What the system observed this scan:
{summary_text}

Also read these recent journal entries for context:
{get_recent_narrative(entries=2)}

Write a concise trader's journal entry (max 150 words) covering:
1. What is the overall market regime right now (risk-on/risk-off, DXY trend)?
   (Correct relationship — do not invert: a FALLING dollar/DXY tends to push
   EUR/USD and GBP/USD UP and USD/JPY DOWN; a RISING dollar does the reverse.)
2. Which pairs have the clearest setups and why?
3. What changed from the previous scan?
4. Any key levels being tested or broken?
5. What should the trader watch for in the next scan?

Be specific with prices. Write like a professional trader, not a chatbot."""

    try:
        r = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        narrative = r.choices[0].message.content.strip()
    except Exception as e:
        narrative = f"Journal write failed: {e}"

    # Save to database
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        INSERT INTO market_journal (session, pairs_data, narrative, created_at)
        VALUES (?, ?, ?, ?)
    """, (session, summary_text, narrative,
          datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    print(f"\n📓 Journal entry written for {session} session")

def get_recent_narrative(entries: int = 5) -> str:
    """
    Read the last N journal entries.
    This is what agents read before making decisions.
    """
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        SELECT session, narrative, created_at
        FROM market_journal
        ORDER BY created_at DESC
        LIMIT ?
    """, (entries,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "No previous journal entries — first scan of this session."

    parts = []
    for session, narrative, ts in reversed(rows):
        time_str = ts[:16] if ts else ""
        parts.append(f"[{time_str} UTC | {session}]\n{narrative}")

    return "\n\n---\n\n".join(parts)

def get_pair_context(pair: str) -> str:
    """
    Get the stored state for a specific pair.
    Used by agents to understand what has been happening with this pair.
    """
    # Get recent journal mentions of this pair
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        SELECT narrative, created_at FROM market_journal
        WHERE pairs_data LIKE ?
        ORDER BY created_at DESC LIMIT 3
    """, (f"%{pair}%",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return f"No recent history for {pair}."

    # Get recent signal outcomes for this pair
    c2 = sqlite3.connect(DB_PATH).cursor()
    c2.execute("""
        SELECT direction, outcome, pips_result, created_at
        FROM signals
        WHERE pair=? AND outcome != 'PENDING'
        ORDER BY created_at DESC LIMIT 5
    """, (pair,))
    outcomes = c2.fetchall()

    context_parts = [f"Recent {pair} market context:"]

    if outcomes:
        context_parts.append("Last 5 signals:")
        for direction, outcome, pips, ts in outcomes:
            emoji = "✅" if outcome == "WIN" else "❌"
            context_parts.append(f"  {emoji} {direction} → {outcome} ({pips:+.1f} pips) at {ts[:10]}")

    # Mention of pair in recent journals
    if rows:
        context_parts.append(f"\nFrom recent scans:")
        for narrative, ts in rows[:2]:
            # Extract just the line mentioning this pair
            for line in narrative.split("\n"):
                if pair in line:
                    context_parts.append(f"  {line.strip()}")
                    break

    return "\n".join(context_parts)

def update_pair_state(pair: str, regime: str, bias: str,
                      key_level: float, atr: float, notes: str):
    """Update the stored state for a pair after analysis."""
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO pair_state
        (pair, regime, bias, key_level, atr, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (pair, regime, bias, key_level, atr, notes,
          datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

def get_performance_context() -> str:
    """
    Summary of recent system performance.
    Fed to Trade Advisor so it knows what has been working.
    """
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        SELECT pair, direction, outcome, pips_result, setup_type, session
        FROM signals
        WHERE outcome != 'PENDING'
        AND created_at > datetime('now', '-7 days')
        ORDER BY created_at DESC
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "No completed trades yet — system is in learning phase."

    stats  = {}
    by_setup  = {}
    by_session = {}

    for pair, direction, outcome, pips, setup, session in rows:
        # By pair
        if pair not in stats:
            stats[pair] = {"wins": 0, "losses": 0, "pips": 0}
        if outcome == "WIN":
            stats[pair]["wins"]  += 1
        else:
            stats[pair]["losses"] += 1
        stats[pair]["pips"] += (pips or 0)

        # By setup type
        if setup:
            if setup not in by_setup:
                by_setup[setup] = {"wins": 0, "total": 0}
            by_setup[setup]["total"] += 1
            if outcome == "WIN":
                by_setup[setup]["wins"] += 1

        # By session
        if session:
            if session not in by_session:
                by_session[session] = {"wins": 0, "total": 0}
            by_session[session]["total"] += 1
            if outcome == "WIN":
                by_session[session]["wins"] += 1

    lines = ["📊 Last 7 days performance:"]

    for pair, s in sorted(stats.items()):
        total = s["wins"] + s["losses"]
        wr    = round(s["wins"] / total * 100) if total > 0 else 0
        lines.append(
            f"  {pair}: {s['wins']}W/{s['losses']}L "
            f"({wr}% WR) | {s['pips']:+.1f} pips"
        )

    if by_setup:
        lines.append("Best setups:")
        for setup, s in sorted(by_setup.items(),
                               key=lambda x: x[1]["wins"]/max(x[1]["total"],1),
                               reverse=True):
            wr = round(s["wins"] / s["total"] * 100)
            lines.append(f"  {setup}: {wr}% win rate ({s['total']} signals)")

    if by_session:
        lines.append("Best sessions:")
        for sess, s in sorted(by_session.items(),
                              key=lambda x: x[1]["wins"]/max(x[1]["total"],1),
                              reverse=True):
            wr = round(s["wins"] / s["total"] * 100)
            lines.append(f"  {sess}: {wr}% win rate")

    return "\n".join(lines)

if __name__ == "__main__":
    init_memory_db()
    print("Memory system initialised")
    print("\nRecent narrative:")
    print(get_recent_narrative())
