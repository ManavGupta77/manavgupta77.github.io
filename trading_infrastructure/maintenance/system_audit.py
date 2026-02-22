"""
============================================================
ALGOSYSTEM DIAGNOSTIC AUDIT
============================================================
Run from project root:
    cd D:\Rajat\Algo_System
    python system_audit.py

Generates: system_audit_report.txt
============================================================
"""

import os
import sys
import sqlite3
import hashlib
import json
from pathlib import Path
from datetime import datetime

# ── Setup ──
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = Path(r"C:\Rajat\trading_infrastructure\storage\databases\algo_trading.db")
REPORT = []


def log(msg=""):
    REPORT.append(msg)
    print(msg)


def section(title):
    log(f"\n{'='*70}")
    log(f"  {title}")
    log(f"{'='*70}")


def subsection(title):
    log(f"\n  --- {title} ---")


# ══════════════════════════════════════════════════════════
# TEST 1: FIND ALL PYTHON FILES (Duplicate Detection)
# ══════════════════════════════════════════════════════════

section("TEST 1: ALL PYTHON FILES IN PROJECT")

py_files = []
for root, dirs, files in os.walk(PROJECT_ROOT):
    # Skip common non-project dirs
    skip = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env'}
    dirs[:] = [d for d in dirs if d not in skip]
    for f in files:
        if f.endswith('.py'):
            full = Path(root) / f
            rel = full.relative_to(PROJECT_ROOT)
            size = full.stat().st_size
            py_files.append((str(rel), size, full))

# Group by filename to find duplicates
from collections import defaultdict
by_name = defaultdict(list)
for rel, size, full in sorted(py_files):
    by_name[full.name].append((rel, size, full))
    log(f"  {rel:55s}  {size:>8,} bytes")

log(f"\n  Total Python files: {len(py_files)}")


# ══════════════════════════════════════════════════════════
# TEST 2: DUPLICATE FILE DETECTION
# ══════════════════════════════════════════════════════════

section("TEST 2: DUPLICATE FILENAME DETECTION")

found_dupes = False
for name, entries in sorted(by_name.items()):
    if len(entries) > 1:
        found_dupes = True
        log(f"\n  ⚠️  DUPLICATE FILENAME: {name}")
        for rel, size, full in entries:
            log(f"      {rel}  ({size:,} bytes)")

        # Check if content is identical
        hashes = []
        for rel, size, full in entries:
            h = hashlib.md5(full.read_bytes()).hexdigest()
            hashes.append((rel, h))

        if len(set(h for _, h in hashes)) == 1:
            log(f"      → IDENTICAL content (same MD5)")
        else:
            log(f"      → DIFFERENT content!")
            for rel, h in hashes:
                log(f"        {rel}: {h}")

if not found_dupes:
    log("  ✅ No duplicate filenames found")


# ══════════════════════════════════════════════════════════
# TEST 3: BREEZE COLLECTOR SCRIPT ANALYSIS
# ══════════════════════════════════════════════════════════

section("TEST 3: BREEZE/OPTIONS COLLECTOR SCRIPTS")

collector_keywords = ['breeze', 'options_collector', 'options_data', 'backfill']
collector_files = []

for rel, size, full in py_files:
    name_lower = full.name.lower()
    if any(kw in name_lower for kw in collector_keywords):
        collector_files.append((rel, size, full))

if not collector_files:
    log("  ❌ No collector scripts found!")
else:
    for rel, size, full in collector_files:
        log(f"\n  📄 {rel} ({size:,} bytes)")
        content = full.read_text(encoding='utf-8', errors='replace')

        # Check critical markers
        checks = {
            "EXPIRY_CHANGE_DATE": None,
            "T03:45": "UTC timestamps (CORRECT)",
            "T09:15": "IST timestamps (WRONG!)",
            "T10:00": "UTC end time",
            "T15:30": "IST end time (WRONG!)",
            "get_historical_data_v2": "Uses v2 API",
            "get_historical_data(": "Uses v1 API",
            "ensure_table": "Creates own table",
            "options_ohlc": "References options_ohlc",
        }

        # Extract EXPIRY_CHANGE_DATE value
        for line in content.split('\n'):
            if 'EXPIRY_CHANGE_DATE' in line and 'date(' in line:
                log(f"      EXPIRY_CHANGE_DATE = {line.strip()}")

            if 'from_str' in line and 'strftime' in line:
                log(f"      from_str pattern: {line.strip()}")

            if 'to_str' in line and 'strftime' in line:
                log(f"      to_str pattern: {line.strip()}")

        for marker, desc in checks.items():
            if marker in content:
                if desc:
                    log(f"      ✓ Contains: {marker} → {desc}")


# ══════════════════════════════════════════════════════════
# TEST 4: DATABASE - TABLE LIST
# ══════════════════════════════════════════════════════════

section("TEST 4: DATABASE TABLES")

if not DB_PATH.exists():
    log(f"  ❌ Database not found at: {DB_PATH}")
else:
    log(f"  DB Path: {DB_PATH}")
    log(f"  DB Size: {DB_PATH.stat().st_size / (1024*1024):.1f} MB")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # List all tables
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    log(f"  Tables: {len(tables)}")
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        log(f"    {t:30s}  {count:>12,} rows")


    # ══════════════════════════════════════════════════════════
    # TEST 5: OPTIONS_OHLC SCHEMA & DATA
    # ══════════════════════════════════════════════════════════

    section("TEST 5: OPTIONS_OHLC TABLE ANALYSIS")

    if 'options_ohlc' not in tables:
        log("  ❌ options_ohlc table does NOT exist in database!")
    else:
        # Schema
        subsection("Schema")
        cols = conn.execute("PRAGMA table_info(options_ohlc)").fetchall()
        for c in cols:
            log(f"    {c['name']:20s}  {c['type']:10s}  {'PK' if c['pk'] else ''}")

        # Indexes
        subsection("Indexes")
        indexes = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='options_ohlc'"
        ).fetchall()
        for idx in indexes:
            log(f"    {idx['name']}")
            if idx['sql']:
                log(f"      {idx['sql']}")

        # Row count
        total = conn.execute("SELECT COUNT(*) FROM options_ohlc").fetchone()[0]
        log(f"\n  Total rows: {total:,}")

        if total > 0:
            # Instruments
            subsection("Instruments")
            rows = conn.execute(
                "SELECT instrument, COUNT(*) as cnt FROM options_ohlc GROUP BY instrument ORDER BY cnt DESC"
            ).fetchall()
            for r in rows:
                log(f"    {r['instrument']:15s}  {r['cnt']:>12,} rows")

            # Expiry summary
            subsection("Expiry Summary (first 20 & last 10)")
            rows = conn.execute("""
                SELECT expiry,
                       COUNT(DISTINCT strike) as strikes,
                       COUNT(DISTINCT option_type) as types,
                       COUNT(DISTINCT DATE(timestamp)) as days,
                       COUNT(*) as candles,
                       MIN(timestamp) as first_ts,
                       MAX(timestamp) as last_ts
                FROM options_ohlc
                GROUP BY expiry
                ORDER BY expiry
            """).fetchall()
            log(f"    Total expiries: {len(rows)}")
            log(f"    {'Expiry':12s} {'Strikes':>8s} {'Types':>6s} {'Days':>5s} {'Candles':>10s}  First TS")
            log(f"    {'-'*70}")

            show_rows = rows[:20] + [None] + rows[-10:] if len(rows) > 30 else rows
            for r in show_rows:
                if r is None:
                    log(f"    {'... (truncated) ...':^70s}")
                else:
                    log(f"    {r['expiry']:12s} {r['strikes']:>8d} {r['types']:>6d} {r['days']:>5d} {r['candles']:>10,}  {r['first_ts'][:19]}")

            # Sample instrument_key format
            subsection("Instrument Key Format (sample 5)")
            rows = conn.execute(
                "SELECT DISTINCT instrument_key FROM options_ohlc LIMIT 5"
            ).fetchall()
            for r in rows:
                log(f"    {r['instrument_key']}")

            # Sample tradingsymbol format
            subsection("TradingSymbol Format (sample 5)")
            rows = conn.execute(
                "SELECT DISTINCT tradingsymbol FROM options_ohlc LIMIT 5"
            ).fetchall()
            for r in rows:
                log(f"    {r['tradingsymbol']}")

            # Timestamp format check
            subsection("Timestamp Format (sample 5)")
            rows = conn.execute(
                "SELECT DISTINCT timestamp FROM options_ohlc ORDER BY timestamp LIMIT 5"
            ).fetchall()
            for r in rows:
                log(f"    {r['timestamp']}")

            # Data quality: zero close prices
            subsection("Data Quality")
            zero_close = conn.execute(
                "SELECT COUNT(*) FROM options_ohlc WHERE close = 0 OR close IS NULL"
            ).fetchone()[0]
            log(f"    Rows with zero/null close: {zero_close:,} ({100*zero_close/total:.1f}%)")

            zero_vol = conn.execute(
                "SELECT COUNT(*) FROM options_ohlc WHERE volume = 0 OR volume IS NULL"
            ).fetchone()[0]
            log(f"    Rows with zero/null volume: {zero_vol:,} ({100*zero_vol/total:.1f}%)")

            zero_oi = conn.execute(
                "SELECT COUNT(*) FROM options_ohlc WHERE oi = 0 OR oi IS NULL"
            ).fetchone()[0]
            log(f"    Rows with zero/null OI: {zero_oi:,} ({100*zero_oi/total:.1f}%)")


    # ══════════════════════════════════════════════════════════
    # TEST 6: MARKET_DATA TABLE
    # ══════════════════════════════════════════════════════════

    section("TEST 6: MARKET_DATA TABLE ANALYSIS")

    if 'market_data' not in tables:
        log("  ❌ market_data table does NOT exist!")
    else:
        total_md = conn.execute("SELECT COUNT(*) FROM market_data").fetchone()[0]
        log(f"  Total rows: {total_md:,}")

        if total_md > 0:
            subsection("Symbols")
            rows = conn.execute(
                "SELECT symbol, COUNT(*) as cnt, MIN(timestamp) as first_ts, MAX(timestamp) as last_ts "
                "FROM market_data GROUP BY symbol ORDER BY cnt DESC"
            ).fetchall()
            for r in rows:
                log(f"    {r['symbol']:20s}  {r['cnt']:>10,} rows  {r['first_ts'][:10]} to {r['last_ts'][:10]}")


    # ══════════════════════════════════════════════════════════
    # TEST 7: ALL OTHER TABLES - ROW COUNTS & SCHEMAS
    # ══════════════════════════════════════════════════════════

    section("TEST 7: ALL TABLE SCHEMAS")

    for t in tables:
        subsection(f"Table: {t}")
        cols = conn.execute(f"PRAGMA table_info([{t}])").fetchall()
        for c in cols:
            pk = " PK" if c['pk'] else ""
            dflt = f" DEFAULT {c['dflt_value']}" if c['dflt_value'] else ""
            nn = " NOT NULL" if c['notnull'] else ""
            log(f"    {c['name']:25s}  {c['type']:12s}{pk}{nn}{dflt}")

    # ══════════════════════════════════════════════════════════
    # TEST 8: CHECK FOR SCHEMA DRIFT
    # ══════════════════════════════════════════════════════════

    section("TEST 8: SCHEMA DRIFT CHECK")
    log("  Comparing options_ohlc in collector ensure_table() vs actual DB...")

    if 'options_ohlc' in tables:
        expected_cols = [
            'timestamp', 'instrument_key', 'tradingsymbol', 'instrument',
            'expiry', 'strike', 'option_type', 'open', 'high', 'low',
            'close', 'volume', 'oi'
        ]
        actual_cols = [c['name'] for c in conn.execute("PRAGMA table_info(options_ohlc)").fetchall()]

        missing = set(expected_cols) - set(actual_cols)
        extra = set(actual_cols) - set(expected_cols)

        if missing:
            log(f"  ⚠️  MISSING columns (in script but not DB): {missing}")
        if extra:
            log(f"  ⚠️  EXTRA columns (in DB but not script): {extra}")
        if not missing and not extra:
            log(f"  ✅ Schema matches: {len(actual_cols)} columns")
            log(f"     Columns: {', '.join(actual_cols)}")

    conn.close()


# ══════════════════════════════════════════════════════════
# TEST 9: CONFIG/.ENV BREEZE KEYS CHECK
# ══════════════════════════════════════════════════════════

section("TEST 9: BREEZE CONFIG CHECK")

ENV_PATH = Path(r"C:\Rajat\trading_infrastructure\config\.env")
if ENV_PATH.exists():
    env_content = ENV_PATH.read_text()
    breeze_keys = ['BREEZE_API_KEY', 'BREEZE_SECRET_KEY']
    for key in breeze_keys:
        if key in env_content:
            # Just check presence, don't log actual value
            for line in env_content.split('\n'):
                if line.startswith(key + '='):
                    val = line.split('=', 1)[1].strip()
                    log(f"  ✅ {key} = {'*' * min(8, len(val))}... ({len(val)} chars)")
        else:
            log(f"  ❌ {key} NOT FOUND in .env")
else:
    log(f"  ❌ .env file not found at {env_path}")

# Check if Config class has Breeze properties
config_path = PROJECT_ROOT / "core" / "config.py"
if config_path.exists():
    config_content = config_path.read_text()
    if 'BREEZE_API_KEY' in config_content and 'os.getenv("BREEZE_API_KEY"' in config_content:
        log(f"  ✅ Config class has BREEZE_API_KEY property")
    else:
        log(f"  ⚠️  Config class does NOT have BREEZE_API_KEY property")

    if 'BREEZE_SECRET_KEY' in config_content and 'os.getenv("BREEZE_SECRET_KEY"' in config_content:
        log(f"  ✅ Config class has BREEZE_SECRET_KEY property")
    else:
        log(f"  ⚠️  Config class does NOT have BREEZE_SECRET_KEY property")


# ══════════════════════════════════════════════════════════
# TEST 10: DATA FOLDER INVENTORY
# ══════════════════════════════════════════════════════════

section("TEST 10: DATA FOLDER CONTENTS")

data_dir = PROJECT_ROOT / "data"
if data_dir.exists():
    for item in sorted(data_dir.iterdir()):
        if item.is_file():
            size_mb = item.stat().st_size / (1024 * 1024)
            log(f"    {item.name:45s}  {size_mb:>8.2f} MB")
        elif item.is_dir():
            count = sum(1 for _ in item.rglob('*') if _.is_file())
            log(f"    {item.name + '/':45s}  ({count} files)")


# ══════════════════════════════════════════════════════════
# TEST 11: PROJECT DIRECTORY STRUCTURE
# ══════════════════════════════════════════════════════════

section("TEST 11: TOP-LEVEL DIRECTORY STRUCTURE")

skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}
for item in sorted(PROJECT_ROOT.iterdir()):
    if item.name in skip_dirs:
        continue
    if item.is_dir():
        sub_count = sum(1 for _ in item.rglob('*.py'))
        log(f"    📁 {item.name}/  ({sub_count} .py files)")
    elif item.is_file() and item.suffix == '.py':
        log(f"    📄 {item.name}")


# ══════════════════════════════════════════════════════════
# SAVE REPORT
# ══════════════════════════════════════════════════════════

section("AUDIT COMPLETE")
report_path = PROJECT_ROOT / "system_audit_report.txt"
report_path.write_text('\n'.join(REPORT), encoding='utf-8')
log(f"\n  Report saved to: {report_path}")
log(f"  Share this file in your next message for analysis.")