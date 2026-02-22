# ==============================================================================
# CLEANUP SCRIPT — trading_infrastructure
# ==============================================================================
# Safely cleans up code duplication and legacy artifacts identified in review.
#
# WHAT THIS DOES (in order):
#   1. Creates a full backup of src/ before touching anything
#   2. Deletes legacy/duplicate files and folders
#   3. Removes duplicate broker singleton lines (broker = X() appearing twice)
#   4. Fixes hardcoded absolute paths in settings.py
#   5. Prints a full summary of every change made
#
# WHAT THIS DOES NOT DO:
#   - Does not touch any sprint architecture files (execution/, simulation_lab/,
#     strategies/, indicators/, market_feeds/live_feeds/)
#   - Does not modify any .env or config files
#   - Does not touch the database or storage/
#
# USAGE:
#   cd C:\Rajat\trading_infrastructure
#   python cleanup_trading_system.py
#
#   To preview without making changes (dry run):
#   python cleanup_trading_system.py --dry-run
# ==============================================================================

import os
import re
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# ==============================================================================
# CONFIG — set your project root here if running from a different directory
# ==============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent

# ==============================================================================
# TARGETS
# ==============================================================================

# Files to DELETE outright
FILES_TO_DELETE = [
    # Old base strategy backup (today's date in filename, superseded by sprint BaseStrategy)
    "src/strategies/base_strategy_backup_20260221_063934.py",
]

# Folders to DELETE outright (entire tree)
FOLDERS_TO_DELETE = [
    # Legacy paper trading — superseded by src/execution/paper_handler.py
    "src/broker_gateway/paper_trading",
]

# Broker connector files that have a duplicate singleton at the bottom
# Pattern: the class is instantiated twice on consecutive (or near-consecutive) lines
# e.g.  broker = ShoonyaBroker()
#        broker = ShoonyaBroker()   ← this second one gets removed
BROKER_FILES_WITH_DUPLICATE_SINGLETON = [
    "src/broker_gateway/broker_shoonya/connector.py",
    "src/broker_gateway/broker_angel/connector.py",
    "src/broker_gateway/broker_upstox/connector.py",
    "src/broker_gateway/broker_dhan/connector.py",
    "src/broker_gateway/broker_flattrade/connector.py",
    "src/broker_gateway/broker_kotak/connector.py",
    "src/broker_gateway/broker_zerodha/connector.py",
]

# Hardcoded path fixes in settings.py
SETTINGS_FILE = "src/config_loader/settings.py"

HARDCODED_PATH_FIXES = [
    {
        "description": "LOG_DIR — hardcoded absolute path → PROJECT_ROOT relative",
        "old": r'    LOG_DIR = Path(r"C:\Rajat\trading_infrastructure\storage\logs\live_trading")',
        "new": '    LOG_DIR = PROJECT_ROOT / "storage" / "logs" / "live_trading"',
    },
    {
        "description": "INSTRUMENT_MASTER — hardcoded absolute path → PROJECT_ROOT relative",
        "old": r'    INSTRUMENT_MASTER = Path(r"C:\Rajat\trading_infrastructure\storage\instrument_masters\derivatives\instrument_master.json")',
        "new": '    INSTRUMENT_MASTER = PROJECT_ROOT / "storage" / "instrument_masters" / "derivatives" / "instrument_master.json"',
    },
]

# ==============================================================================
# HELPERS
# ==============================================================================

class ChangeLog:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.entries = []
        self.errors  = []

    def record(self, category: str, detail: str):
        prefix = "[DRY RUN] " if self.dry_run else "[DONE]    "
        entry = f"{prefix}{category}: {detail}"
        self.entries.append(entry)
        print(entry)

    def error(self, detail: str):
        entry = f"[ERROR]   {detail}"
        self.errors.append(entry)
        print(entry)

    def print_summary(self):
        print()
        print("=" * 70)
        print("  CLEANUP SUMMARY")
        print("=" * 70)
        print(f"  Mode      : {'DRY RUN — no files were changed' if self.dry_run else 'LIVE — changes applied'}")
        print(f"  Changes   : {len(self.entries)}")
        print(f"  Errors    : {len(self.errors)}")
        print()

        if self.entries:
            print("  Changes applied:")
            for e in self.entries:
                print(f"    {e}")

        if self.errors:
            print()
            print("  Errors (review manually):")
            for e in self.errors:
                print(f"    {e}")

        print()
        if not self.dry_run and not self.errors:
            print("  All cleanup steps completed successfully.")
        elif self.dry_run:
            print("  Re-run without --dry-run to apply changes.")
        else:
            print("  Some steps had errors — review above before proceeding.")
        print("=" * 70)


def resolve(relative_path: str) -> Path:
    return PROJECT_ROOT / relative_path


def create_backup(dry_run: bool, log: ChangeLog) -> Path:
    """
    Creates a timestamped zip backup of src/ before any changes are made.
    Returns the backup path.
    """
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PROJECT_ROOT / "maintenance" / "backups"
    backup_zip = backup_dir / f"src_backup_{timestamp}"

    if dry_run:
        log.record("BACKUP", f"Would create backup at {backup_zip}.zip")
        return backup_zip

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        src_path = PROJECT_ROOT / "src"
        shutil.make_archive(str(backup_zip), "zip", str(src_path.parent), "src")
        log.record("BACKUP", f"Created {backup_zip}.zip")
        return backup_zip
    except Exception as e:
        log.error(f"Backup failed: {e}")
        print()
        print("  Backup failed — aborting to protect your files.")
        print("  Fix the backup issue or create a manual backup before retrying.")
        sys.exit(1)


# ==============================================================================
# STEP 1 — Delete files
# ==============================================================================

def delete_files(dry_run: bool, log: ChangeLog):
    print()
    print("── Step 1: Delete legacy files ─────────────────────────────────────")

    for rel_path in FILES_TO_DELETE:
        p = resolve(rel_path)
        if not p.exists():
            log.record("SKIP", f"{rel_path} (not found — already clean)")
            continue
        if dry_run:
            log.record("DELETE FILE", rel_path)
        else:
            try:
                p.unlink()
                log.record("DELETE FILE", rel_path)
            except Exception as e:
                log.error(f"Could not delete {rel_path}: {e}")


# ==============================================================================
# STEP 2 — Delete folders
# ==============================================================================

def delete_folders(dry_run: bool, log: ChangeLog):
    print()
    print("── Step 2: Delete legacy folders ───────────────────────────────────")

    for rel_path in FOLDERS_TO_DELETE:
        p = resolve(rel_path)
        if not p.exists():
            log.record("SKIP", f"{rel_path}/ (not found — already clean)")
            continue

        # Count files inside so the log is informative
        file_count = sum(1 for _ in p.rglob("*") if _.is_file())

        if dry_run:
            log.record("DELETE FOLDER", f"{rel_path}/ ({file_count} files)")
        else:
            try:
                shutil.rmtree(p)
                log.record("DELETE FOLDER", f"{rel_path}/ ({file_count} files removed)")
            except Exception as e:
                log.error(f"Could not delete {rel_path}/: {e}")


# ==============================================================================
# STEP 3 — Remove duplicate broker singletons
# ==============================================================================

def remove_duplicate_singletons(dry_run: bool, log: ChangeLog):
    """
    Each broker connector ends with two identical lines like:
        broker = ShoonyaBroker()
        broker = ShoonyaBroker()

    This finds the LAST occurrence of `broker = <ClassName>()` in the file
    and removes it, keeping the first (real) instantiation.

    Strategy:
      - Find all lines matching `^broker = \w+\(\)\s*$`
      - If exactly 2 found and they are identical, remove the last one
      - If pattern doesn't match expectations, skip and report
    """
    print()
    print("── Step 3: Remove duplicate broker singletons ──────────────────────")

    singleton_pattern = re.compile(r'^broker\s*=\s*\w+\(\)\s*$')

    for rel_path in BROKER_FILES_WITH_DUPLICATE_SINGLETON:
        p = resolve(rel_path)
        if not p.exists():
            log.record("SKIP", f"{rel_path} (file not found)")
            continue

        try:
            original_text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.error(f"Could not read {rel_path}: {e}")
            continue

        lines = original_text.splitlines(keepends=True)

        # Find line numbers (0-indexed) of all singleton lines
        singleton_lines = [
            i for i, line in enumerate(lines)
            if singleton_pattern.match(line)
        ]

        if len(singleton_lines) < 2:
            log.record("SKIP", f"{rel_path} (no duplicate found — already clean)")
            continue

        if len(singleton_lines) > 2:
            log.error(f"{rel_path}: found {len(singleton_lines)} singleton lines (expected 2) — review manually")
            continue

        first_idx, second_idx = singleton_lines[0], singleton_lines[1]
        first_line  = lines[first_idx].strip()
        second_line = lines[second_idx].strip()

        if first_line != second_line:
            log.error(
                f"{rel_path}: two singleton lines but they differ "
                f"(line {first_idx+1}: '{first_line}' vs line {second_idx+1}: '{second_line}') "
                f"— review manually"
            )
            continue

        # Remove the second (duplicate) singleton line
        cleaned_lines = [line for i, line in enumerate(lines) if i != second_idx]

        # Also strip any trailing blank line that was only there as padding for the duplicate
        # (optional cosmetic: if the line before the removed line is blank, remove that too)
        # Only do this if the removed line was truly the last non-empty content
        cleaned_text = "".join(cleaned_lines)

        if dry_run:
            log.record(
                "REMOVE DUPLICATE SINGLETON",
                f"{rel_path} — line {second_idx+1}: '{second_line.strip()}'"
            )
        else:
            try:
                p.write_text(cleaned_text, encoding="utf-8")
                log.record(
                    "REMOVE DUPLICATE SINGLETON",
                    f"{rel_path} — removed line {second_idx+1}: '{second_line.strip()}'"
                )
            except Exception as e:
                log.error(f"Could not write {rel_path}: {e}")


# ==============================================================================
# STEP 4 — Fix hardcoded paths in settings.py
# ==============================================================================

def fix_hardcoded_paths(dry_run: bool, log: ChangeLog):
    print()
    print("── Step 4: Fix hardcoded paths in settings.py ──────────────────────")

    p = resolve(SETTINGS_FILE)
    if not p.exists():
        log.error(f"{SETTINGS_FILE} not found — skipping path fixes")
        return

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.error(f"Could not read {SETTINGS_FILE}: {e}")
        return

    modified = text
    any_change = False

    for fix in HARDCODED_PATH_FIXES:
        if fix["old"] in modified:
            modified = modified.replace(fix["old"], fix["new"])
            any_change = True
            if dry_run:
                log.record("FIX PATH", fix["description"])
            else:
                log.record("FIX PATH", fix["description"])
        else:
            # Check if it's already fixed (new value present)
            if fix["new"] in modified:
                log.record("SKIP", f"{fix['description']} (already fixed)")
            else:
                log.error(
                    f"Could not find target string for: {fix['description']} "
                    f"— review {SETTINGS_FILE} manually"
                )

    if any_change and not dry_run:
        try:
            p.write_text(modified, encoding="utf-8")
        except Exception as e:
            log.error(f"Could not write {SETTINGS_FILE}: {e}")


# ==============================================================================
# STEP 5 — Verify sprint architecture files are untouched
# ==============================================================================

SPRINT_FILES_MUST_EXIST = [
    "src/execution/backtest_execution_handler.py",
    "src/execution/paper_handler.py",
    "src/simulation_lab/market_session.py",
    "src/simulation_lab/backtest_runner.py",
    "src/strategies/options_selling/iron_straddle.py",
    "src/strategies/risk/risk_guard.py",
    "src/indicators/indicator_engine.py",
    "src/market_feeds/live_feeds/tick_replay.py",
]

def verify_sprint_files(log: ChangeLog):
    print()
    print("── Step 5: Verify sprint architecture files are intact ─────────────")

    all_ok = True
    for rel_path in SPRINT_FILES_MUST_EXIST:
        p = resolve(rel_path)
        if p.exists():
            log.record("VERIFY OK", rel_path)
        else:
            log.error(f"MISSING: {rel_path} — sprint file not found!")
            all_ok = False

    if all_ok:
        print("  All sprint architecture files present and accounted for.")


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Cleanup script for trading_infrastructure — removes legacy code and duplication."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview all changes without modifying any files."
    )
    args = parser.parse_args()

    dry_run = args.dry_run
    log = ChangeLog(dry_run=dry_run)

    print()
    print("=" * 70)
    print("  TRADING INFRASTRUCTURE CLEANUP")
    print(f"  Project root : {PROJECT_ROOT}")
    print(f"  Mode         : {'DRY RUN — no files will be changed' if dry_run else 'LIVE — changes will be applied'}")
    print(f"  Timestamp    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    if not dry_run:
        print()
        print("  Creating backup before making any changes...")
        create_backup(dry_run=False, log=log)
    else:
        create_backup(dry_run=True, log=log)

    delete_files(dry_run, log)
    delete_folders(dry_run, log)
    remove_duplicate_singletons(dry_run, log)
    fix_hardcoded_paths(dry_run, log)
    verify_sprint_files(log)

    log.print_summary()


if __name__ == "__main__":
    main()
