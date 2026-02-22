# ==========================================
# DASHBOARD.PY
# ==========================================
# Purpose:
#   Streamlit web dashboard for monitoring and controlling the
#   algo trading system. Reads from position_manager.py for rich
#   straddle tracking with live P&L, roll history, and strategy stats.
#
# Sections:
#   - Sidebar: System status, server health, quick action buttons
#   - Live Straddle Monitor: Open positions with real-time P&L
#   - Roll History: Every roll event with P&L impact
#   - Strategy Performance: Win/loss, avg P&L, total rolls
#   - Closed Straddles: Historical trades with P&L
#   - Full Trade Ledger: Raw trade log from paper_ledger.csv
#
# Data Sources:
#   - position_manager.py (positions.json) for straddle state
#   - paper_ledger.csv for raw trade log
#   - webhook_receiver.py /health endpoint for server status
#   - paper_trade.py get_ltp() for live prices
#
# Usage:
#   streamlit run dashboard.py
# ==========================================

import streamlit as st
import pandas as pd
import datetime
import os
import requests
import json

# ==========================================
# PAGE CONFIG
# ==========================================
st.set_page_config(
    page_title="Algo Trading Control Tower",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# File paths
LEDGER_FILE = "paper_ledger.csv"
POSITIONS_FILE = "positions.json"
WEBHOOK_URL = "http://127.0.0.1:8000"

LEDGER_COLUMNS = [
    "timestamp", "symbol", "token", "exchange",
    "side", "quantity", "fill_price", "total_value",
    "strategy", "status"
]


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def load_ledger():
    """Load the paper ledger CSV with error handling."""
    if not os.path.exists(LEDGER_FILE):
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    try:
        df = pd.read_csv(LEDGER_FILE, names=LEDGER_COLUMNS, header=0)
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df['fill_price'] = pd.to_numeric(df['fill_price'], errors='coerce').fillna(0)
        df['total_value'] = pd.to_numeric(df['total_value'], errors='coerce').fillna(0)
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"Error loading ledger: {e}")
        return pd.DataFrame(columns=LEDGER_COLUMNS)


def load_positions():
    """Load positions.json directly (no import needed)."""
    if not os.path.exists(POSITIONS_FILE):
        return {"positions": {}, "straddles": {}}
    try:
        with open(POSITIONS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading positions: {e}")
        return {"positions": {}, "straddles": {}}


def check_server_health():
    """Check if webhook server is running."""
    try:
        r = requests.get(f"{WEBHOOK_URL}/health", timeout=2)
        return r.status_code == 200, r.json()
    except Exception:
        return False, None


def parse_option_type(symbol):
    """Extract CE/PE from symbol name."""
    if str(symbol).endswith("CE"):
        return "CE"
    elif str(symbol).endswith("PE"):
        return "PE"
    return "?"


def parse_strike(symbol):
    """Extract strike price from symbol."""
    s = str(symbol)
    if s.endswith("CE") or s.endswith("PE"):
        s = s[:-2]
    digits = ""
    for ch in reversed(s):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    try:
        return int(digits)
    except ValueError:
        return 0


# ==========================================
# SIDEBAR
# ==========================================

st.sidebar.header("System Status")

system_status = st.sidebar.radio(
    "Master Switch",
    ["OFF", "PAPER TRADING", "LIVE TRADING"],
    index=0
)

if system_status == "LIVE TRADING":
    st.sidebar.error("⚠️ CAUTION: LIVE MONEY MODE")
elif system_status == "PAPER TRADING":
    st.sidebar.success("🟢 Simulation Mode Active")
else:
    st.sidebar.warning("🛑 System Halted")

st.sidebar.divider()

# Server Health
st.sidebar.subheader("Server Health")
server_ok, health_data = check_server_health()

if server_ok:
    st.sidebar.success("🟢 Webhook Server Online")
    st.sidebar.caption(f"Server time: {health_data.get('server_time', 'N/A')}")
else:
    st.sidebar.error("🔴 Webhook Server Offline")
    st.sidebar.caption("Start: `python webhook_receiver.py`")

st.sidebar.divider()

# Quick Actions
st.sidebar.subheader("Quick Actions")
col_a, col_b = st.sidebar.columns(2)

with col_a:
    if st.button("🔴 SELL Straddle", use_container_width=True):
        if server_ok:
            try:
                r = requests.post(f"{WEBHOOK_URL}/webhook", json={
                    "index": "NIFTY",
                    "action": "SELL_STRADDLE",
                    "qty_lots": 1,
                    "strategy": "Dashboard_Manual"
                }, timeout=10)
                if r.status_code == 200:
                    st.sidebar.success("✅ Straddle SOLD!")
                else:
                    st.sidebar.error(f"Failed: {r.json().get('error', 'Unknown')}")
            except Exception as e:
                st.sidebar.error(f"Error: {e}")
        else:
            st.sidebar.error("Server offline!")

with col_b:
    if st.button("🟢 EXIT Straddle", use_container_width=True):
        if server_ok:
            try:
                r = requests.post(f"{WEBHOOK_URL}/webhook", json={
                    "index": "NIFTY",
                    "action": "EXIT_STRADDLE",
                    "qty_lots": 1,
                    "strategy": "Dashboard_Manual"
                }, timeout=10)
                if r.status_code == 200:
                    st.sidebar.success("✅ Straddle EXITED!")
                else:
                    st.sidebar.error(f"Failed: {r.json().get('error', 'Unknown')}")
            except Exception as e:
                st.sidebar.error(f"Error: {e}")
        else:
            st.sidebar.error("Server offline!")

st.sidebar.divider()

# Management
st.sidebar.subheader("Data Management")
col_m1, col_m2 = st.sidebar.columns(2)

with col_m1:
    if st.sidebar.button("🗑️ Clear Ledger", use_container_width=True):
        if os.path.exists(LEDGER_FILE):
            os.remove(LEDGER_FILE)
            st.sidebar.success("Ledger cleared!")
            st.rerun()

with col_m2:
    if st.sidebar.button("🗑️ Clear Positions", use_container_width=True):
        if os.path.exists(POSITIONS_FILE):
            os.remove(POSITIONS_FILE)
            st.sidebar.success("Positions cleared!")
            st.rerun()


# ==========================================
# MAIN DISPLAY
# ==========================================

st.title("🚀 Algo Trading Control Tower")

# Load data
data = load_positions()
positions = data.get("positions", {})
straddles = data.get("straddles", {})
df = load_ledger()

# Separate open and closed straddles
open_straddles = {k: v for k, v in straddles.items() if v.get("status") == "OPEN"}
closed_straddles = {k: v for k, v in straddles.items() if v.get("status") == "CLOSED"}


# ==========================================
# SECTION 1: LIVE STRADDLE MONITOR
# ==========================================

st.subheader("🔴 Live Straddle Monitor")

if open_straddles:
    for str_id, strad in open_straddles.items():
        ce_leg = positions.get(strad.get("ce_position_id", ""), {})
        pe_leg = positions.get(strad.get("pe_position_id", ""), {})

        ce_entry = ce_leg.get("entry_price", 0)
        pe_entry = pe_leg.get("entry_price", 0)
        entry_premium = strad.get("entry_premium", 0)
        roll_count = strad.get("roll_count", 0)
        entry_time = strad.get("entry_time", "N/A")
        strategy = strad.get("strategy", "N/A")

        # Straddle header
        st.markdown(f"**{str_id}** — Strike: **{strad.get('strike', 'N/A')}** | "
                    f"Strategy: `{strategy}` | Entry: {entry_time}")

        # Metrics row
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("CE Entry", f"₹{ce_entry:.2f}")
        m2.metric("PE Entry", f"₹{pe_entry:.2f}")
        m3.metric("Total Premium", f"₹{entry_premium:.2f}")
        m4.metric("Rolls", roll_count)
        m5.metric("CE Symbol", ce_leg.get("symbol", "N/A")[-12:])
        m6.metric("PE Symbol", pe_leg.get("symbol", "N/A")[-12:])

        # Leg details table
        leg_data = []
        for leg, label in [(ce_leg, "CE"), (pe_leg, "PE")]:
            if leg:
                leg_data.append({
                    "Leg": label,
                    "Symbol": leg.get("symbol", ""),
                    "Token": leg.get("token", ""),
                    "Strike": leg.get("strike", ""),
                    "Side": leg.get("side", ""),
                    "Entry Price": f"₹{leg.get('entry_price', 0):.2f}",
                    "Entry Time": leg.get("entry_time", ""),
                    "Exchange": leg.get("exchange", ""),
                })

        if leg_data:
            st.dataframe(pd.DataFrame(leg_data), use_container_width=True, hide_index=True)

        st.divider()
else:
    st.info("📭 No open straddles. Use the SELL Straddle button or send a TradingView signal.")


# ==========================================
# SECTION 2: STRATEGY PERFORMANCE
# ==========================================

st.subheader("📊 Strategy Performance")

# Collect all strategies
all_strategies = set()
for s in straddles.values():
    all_strategies.add(s.get("strategy", "Unknown"))

if all_strategies:
    for strat_name in sorted(all_strategies):
        strat_straddles = [s for s in straddles.values() if s.get("strategy") == strat_name]
        strat_open = [s for s in strat_straddles if s.get("status") == "OPEN"]
        strat_closed = [s for s in strat_straddles if s.get("status") == "CLOSED"]

        closed_pnl = sum(s.get("pnl", 0) for s in strat_closed)
        wins = sum(1 for s in strat_closed if s.get("pnl", 0) > 0)
        losses = sum(1 for s in strat_closed if s.get("pnl", 0) <= 0)
        total_rolls = sum(s.get("roll_count", 0) for s in strat_straddles)
        win_rate = (wins / len(strat_closed) * 100) if strat_closed else 0
        avg_pnl = (closed_pnl / len(strat_closed)) if strat_closed else 0

        st.markdown(f"**Strategy: `{strat_name}`**")

        p1, p2, p3, p4, p5, p6, p7 = st.columns(7)
        p1.metric("Total", len(strat_straddles))
        p2.metric("Open", len(strat_open))
        p3.metric("Closed", len(strat_closed))
        p4.metric("Closed P&L", f"₹{closed_pnl:.2f}",
                  delta=f"₹{closed_pnl:.2f}" if closed_pnl != 0 else None,
                  delta_color="normal")
        p5.metric("Win Rate", f"{win_rate:.0f}%")
        p6.metric("Avg P&L", f"₹{avg_pnl:.2f}")
        p7.metric("Total Rolls", total_rolls)

        st.divider()
else:
    st.caption("No strategies executed yet.")


# ==========================================
# SECTION 3: ROLL HISTORY
# ==========================================

st.subheader("🔄 Roll History")

# Find all closed positions that were part of a roll (exited but straddle continued)
roll_events = []
for pos_id, pos in positions.items():
    if pos.get("status") == "CLOSED" and pos.get("straddle_id"):
        strad = straddles.get(pos["straddle_id"], {})
        # A rolled leg is one that was closed but its straddle is still open OR has roll_count > 0
        if strad.get("roll_count", 0) > 0:
            # Check if this closed position was replaced (not the final exit)
            current_ce = strad.get("ce_position_id", "")
            current_pe = strad.get("pe_position_id", "")
            if pos_id != current_ce and pos_id != current_pe:
                roll_events.append({
                    "Straddle": pos.get("straddle_id", ""),
                    "Strategy": pos.get("strategy", ""),
                    "Rolled Leg": pos.get("option_type", ""),
                    "Old Symbol": pos.get("symbol", ""),
                    "Old Strike": pos.get("strike", ""),
                    "Entry Price": f"₹{pos.get('entry_price', 0):.2f}",
                    "Exit Price": f"₹{pos.get('exit_price', 0):.2f}",
                    "Roll P&L": f"₹{(pos.get('entry_price', 0) - pos.get('exit_price', 0)):.2f}",
                    "Exit Time": pos.get("exit_time", ""),
                })

if roll_events:
    roll_df = pd.DataFrame(roll_events)
    roll_df = roll_df.sort_values("Exit Time", ascending=False)

    def highlight_roll_pnl(row):
        pnl_str = row.get("Roll P&L", "₹0")
        pnl_val = float(pnl_str.replace("₹", "").replace(",", ""))
        if pnl_val > 0:
            return ['color: #51cf66'] * len(row)
        elif pnl_val < 0:
            return ['color: #ff6b6b'] * len(row)
        return [''] * len(row)

    st.dataframe(
        roll_df.style.apply(highlight_roll_pnl, axis=1),
        use_container_width=True,
        hide_index=True
    )
else:
    st.caption("No rolls executed yet.")


# ==========================================
# SECTION 4: CLOSED STRADDLES
# ==========================================

st.subheader("📜 Closed Straddles")

if closed_straddles:
    closed_data = []
    for str_id, strad in closed_straddles.items():
        pnl = strad.get("pnl", 0)
        closed_data.append({
            "Straddle ID": str_id,
            "Strategy": strad.get("strategy", ""),
            "Strike": strad.get("strike", ""),
            "Entry Premium": f"₹{strad.get('entry_premium', 0):.2f}",
            "Exit Premium": f"₹{strad.get('exit_premium', 0):.2f}",
            "P&L": f"₹{pnl:.2f}",
            "Rolls": strad.get("roll_count", 0),
            "Entry Time": strad.get("entry_time", ""),
            "Exit Time": strad.get("exit_time", ""),
        })

    closed_df = pd.DataFrame(closed_data)
    closed_df = closed_df.sort_values("Exit Time", ascending=False)

    def highlight_pnl(row):
        pnl_str = row.get("P&L", "₹0")
        pnl_val = float(pnl_str.replace("₹", "").replace(",", ""))
        if pnl_val > 0:
            return ['background-color: #0d3b0d'] * len(row)
        elif pnl_val < 0:
            return ['background-color: #3b0d0d'] * len(row)
        return [''] * len(row)

    st.dataframe(
        closed_df.style.apply(highlight_pnl, axis=1),
        use_container_width=True,
        hide_index=True
    )

    # Totals
    total_closed_pnl = sum(s.get("pnl", 0) for s in closed_straddles.values())
    st.markdown(f"**Total Closed P&L: ₹{total_closed_pnl:.2f}**")
else:
    st.caption("No closed straddles yet.")


# ==========================================
# SECTION 5: FULL TRADE LEDGER
# ==========================================

st.divider()
st.subheader("📋 Full Trade Ledger")

if not df.empty:
    # Strategy filter
    all_strat_names = ["All"] + sorted(df['strategy'].dropna().unique().tolist())
    selected_strategy = st.selectbox("Filter by Strategy", all_strat_names)

    display_df = df.copy()
    if selected_strategy != "All":
        display_df = display_df[display_df['strategy'] == selected_strategy]

    display_df['type'] = display_df['symbol'].apply(parse_option_type)
    display_df['strike'] = display_df['symbol'].apply(parse_strike)

    def highlight_side(row):
        if row['side'] == 'SELL':
            return ['color: #ff6b6b'] * len(row)
        elif row['side'] == 'BUY':
            return ['color: #51cf66'] * len(row)
        return [''] * len(row)

    display_cols = [
        'timestamp', 'symbol', 'type', 'strike', 'side',
        'quantity', 'fill_price', 'total_value', 'exchange',
        'strategy', 'status'
    ]
    display_df = display_df[[c for c in display_cols if c in display_df.columns]]
    display_df = display_df.sort_index(ascending=False)

    st.dataframe(
        display_df.style.apply(highlight_side, axis=1),
        use_container_width=True,
        hide_index=True
    )
else:
    st.caption("No trades in ledger yet.")


# ==========================================
# SECTION 6: EXPORT
# ==========================================

st.divider()
col_exp1, col_exp2, col_exp3, _ = st.columns([1, 1, 1, 2])

with col_exp1:
    if not df.empty:
        csv_data = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 Ledger CSV",
            csv_data,
            "paper_ledger_export.csv",
            "text/csv",
            use_container_width=True
        )

with col_exp2:
    if closed_straddles:
        closed_export = pd.DataFrame([
            {
                "straddle_id": k,
                "strategy": v.get("strategy"),
                "strike": v.get("strike"),
                "entry_premium": v.get("entry_premium"),
                "exit_premium": v.get("exit_premium"),
                "pnl": v.get("pnl"),
                "roll_count": v.get("roll_count"),
                "entry_time": v.get("entry_time"),
                "exit_time": v.get("exit_time"),
            }
            for k, v in closed_straddles.items()
        ])
        st.download_button(
            "📥 P&L Report",
            closed_export.to_csv(index=False).encode('utf-8'),
            "straddle_pnl_report.csv",
            "text/csv",
            use_container_width=True
        )

with col_exp3:
    if positions:
        st.download_button(
            "📥 Positions JSON",
            json.dumps(data, indent=2).encode('utf-8'),
            "positions_export.json",
            "application/json",
            use_container_width=True
        )


# ==========================================
# FOOTER
# ==========================================

st.divider()
last_updated = data.get("last_updated", "N/A")
st.caption(
    f"📡 Auto-refresh: 3s | "
    f"Server: {'🟢' if server_ok else '🔴'} | "
    f"Open Straddles: {len(open_straddles)} | "
    f"Positions last updated: {last_updated} | "
    f"Dashboard refresh: {datetime.datetime.now().strftime('%H:%M:%S')}"
)

# Auto-refresh
import time
time.sleep(3)
st.rerun()
