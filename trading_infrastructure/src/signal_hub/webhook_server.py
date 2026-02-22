# ==========================================
# WEBHOOK_RECEIVER.PY
# ==========================================
# Purpose:
#   Flask web server that receives TradingView alerts via HTTP POST,
#   resolves the correct option contracts, and executes paper trades.
#   This is the central orchestrator that connects all modules together.
#
# Endpoints:
#   - POST /webhook          : Receives TradingView JSON signal, resolves
#                              ATM contracts via option_chain.py, executes
#                              both straddle legs via paper_trade.py
#   - GET  /health           : Returns server status, supported indices,
#                              and supported actions (used by dashboard)
#
# Expected TradingView Alert Payload:
#   {
#       "index": "NIFTY",              (Required: NIFTY / BANKNIFTY / BSX)
#       "action": "SELL_STRADDLE",     (Required: SELL_STRADDLE / BUY_STRADDLE / EXIT_STRADDLE)
#       "qty_lots": 1,                 (Optional: number of lots, default 1)
#       "strategy": "MyStrategy"       (Optional: strategy name for tracking)
#   }
#
# Execution Flow:
#   1. Validate incoming JSON payload
#   2. Fetch live spot price from Angel One (via paper_trade.get_ltp)
#   3. Auto-calculate next expiry date (via option_chain.get_next_expiry)
#   4. Resolve ATM CE + PE contracts (via option_chain.get_atm_contracts)
#   5. Execute both legs as paper orders (via paper_trade.place_paper_order)
#   6. Return JSON summary with fill status
#
# Important Notes:
#   - Runs on port 8000 (Ngrok tunnels to this port for external access)
#   - Supports three actions: SELL_STRADDLE, BUY_STRADDLE, EXIT_STRADDLE
#   - All trades are paper/virtual - no real orders hit the exchange
#   - Dashboard sidebar buttons also send requests to this server
#   - Depends on: option_chain.py, paper_trade.py, Flask
#
# Called By:
#   - TradingView alerts (via Ngrok tunnel)
#   - test_webhook.py (for local testing)
#   - dashboard.py sidebar buttons (SELL/EXIT Straddle)
#
# Can Run Standalone:
#   Yes - starts the Flask server on port 8000
#   Usage: python webhook_receiver.py
# ==========================================

from flask import Flask, request, jsonify
import datetime
import option_chain
import paper_trade

app = Flask(__name__)


# ==========================================
# WEBHOOK ENDPOINT
# ==========================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Receives a TradingView alert and executes an ATM Straddle in paper trading mode.

    Expected JSON payload from TradingView:
    {
        "index": "NIFTY",          // Required: "NIFTY", "BANKNIFTY", or "BSX"
        "action": "SELL_STRADDLE", // Required: Action to execute
        "qty_lots": 1,             // Optional: Number of lots (default: 1)
        "strategy": "TV_Signal"    // Optional: Strategy name for logging
    }
    """
    data = request.json
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*60}")
    print(f"📡 SIGNAL RECEIVED at {timestamp}")
    print(f"   Payload: {data}")
    print(f"{'='*60}")

    # --- 1. VALIDATE PAYLOAD ---
    index_name = data.get('index', '').upper()
    action = data.get('action', '').upper()
    qty_lots = data.get('qty_lots', 1)
    strategy = data.get('strategy', 'TV_Signal')

    if not index_name or not action:
        error = "Missing 'index' or 'action' in payload"
        print(f"   ❌ {error}")
        return jsonify({"status": "REJECTED", "error": error}), 400

    cfg = option_chain.INDEX_CONFIG.get(index_name)
    if not cfg:
        error = f"Unknown index: {index_name}"
        print(f"   ❌ {error}")
        return jsonify({"status": "REJECTED", "error": error}), 400

    # --- 2. FETCH LIVE SPOT PRICE ---
    print(f"\n📊 Fetching {index_name} spot price...")
    spot_price = paper_trade.get_ltp(
        cfg["spot_exchange"],
        cfg["spot_symbol"],
        cfg["spot_token"]
    )

    if spot_price == 0:
        error = "Could not fetch spot price"
        print(f"   ❌ {error}")
        return jsonify({"status": "REJECTED", "error": error}), 500

    print(f"   ✅ {index_name} Spot: ₹{spot_price}")

    # --- 3. RESOLVE EXPIRY DATE ---
    expiry = option_chain.get_next_expiry(index_name)
    print(f"   📅 Target Expiry: {expiry}")

    # --- 4. FIND ATM CONTRACTS ---
    print(f"\n🔍 Finding ATM contracts...")
    atm = option_chain.get_atm_contracts(index_name, expiry, spot_price)

    if not atm:
        error = "Could not resolve ATM contracts from master file"
        print(f"   ❌ {error}")
        return jsonify({"status": "REJECTED", "error": error}), 500

    ce = atm["CE"]
    pe = atm["PE"]
    atm_strike = atm["atm_strike"]

    print(f"   ✅ ATM Strike: {atm_strike}")
    print(f"   CE: {ce['symbol']} (Token: {ce['token']})")
    print(f"   PE: {pe['symbol']} (Token: {pe['token']})")

    # --- 5. EXECUTE BASED ON ACTION ---
    exchange = cfg["option_exchange"]
    results = []

    if action == "SELL_STRADDLE":
        print(f"\n🚀 EXECUTING ATM STRADDLE (SELL)...")

        # Leg 1: SELL CE
        ce_result = paper_trade.place_paper_order(
            symbol=ce['symbol'],
            token=str(ce['token']),
            qty=qty_lots,
            side="SELL",
            exchange=exchange,
            strategy=strategy
        )
        results.append({"leg": "CE", "result": ce_result})

        # Leg 2: SELL PE
        pe_result = paper_trade.place_paper_order(
            symbol=pe['symbol'],
            token=str(pe['token']),
            qty=qty_lots,
            side="SELL",
            exchange=exchange,
            strategy=strategy
        )
        results.append({"leg": "PE", "result": pe_result})

    elif action == "BUY_STRADDLE":
        print(f"\n🚀 EXECUTING ATM STRADDLE (BUY)...")

        ce_result = paper_trade.place_paper_order(
            symbol=ce['symbol'],
            token=str(ce['token']),
            qty=qty_lots,
            side="BUY",
            exchange=exchange,
            strategy=strategy
        )
        results.append({"leg": "CE", "result": ce_result})

        pe_result = paper_trade.place_paper_order(
            symbol=pe['symbol'],
            token=str(pe['token']),
            qty=qty_lots,
            side="BUY",
            exchange=exchange,
            strategy=strategy
        )
        results.append({"leg": "PE", "result": pe_result})

    elif action == "EXIT_STRADDLE":
        # To exit a sold straddle, we BUY back both legs
        print(f"\n🚀 EXITING ATM STRADDLE (BUY TO CLOSE)...")

        ce_result = paper_trade.place_paper_order(
            symbol=ce['symbol'],
            token=str(ce['token']),
            qty=qty_lots,
            side="BUY",
            exchange=exchange,
            strategy=strategy
        )
        results.append({"leg": "CE_EXIT", "result": ce_result})

        pe_result = paper_trade.place_paper_order(
            symbol=pe['symbol'],
            token=str(pe['token']),
            qty=qty_lots,
            side="SELL",
            exchange=exchange,
            strategy=strategy
        )
        results.append({"leg": "PE_EXIT", "result": pe_result})

    else:
        error = f"Unknown action: {action}. Use SELL_STRADDLE, BUY_STRADDLE, or EXIT_STRADDLE"
        print(f"   ❌ {error}")
        return jsonify({"status": "REJECTED", "error": error}), 400

    # --- 6. SUMMARY ---
    filled = sum(1 for r in results if r['result'] is not None)
    total_legs = len(results)

    summary = {
        "status": "FILLED" if filled == total_legs else "PARTIAL",
        "index": index_name,
        "action": action,
        "atm_strike": atm_strike,
        "expiry": expiry,
        "spot_price": spot_price,
        "legs_filled": f"{filled}/{total_legs}",
        "strategy": strategy,
        "timestamp": timestamp,
    }

    print(f"\n{'='*60}")
    print(f"📋 ORDER SUMMARY: {summary['status']}")
    print(f"   {filled}/{total_legs} legs filled for {index_name} {action}")
    print(f"   ATM: {atm_strike} | Expiry: {expiry} | Spot: ₹{spot_price}")
    print(f"{'='*60}\n")

    return jsonify(summary), 200


# ==========================================
# HEALTH CHECK ENDPOINT
# ==========================================

@app.route('/health', methods=['GET'])
def health():
    """Quick endpoint to verify server is running."""
    return jsonify({
        "status": "running",
        "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "supported_indices": list(option_chain.INDEX_CONFIG.keys()),
        "supported_actions": ["SELL_STRADDLE", "BUY_STRADDLE", "EXIT_STRADDLE"]
    }), 200


# ==========================================
# START SERVER
# ==========================================
if __name__ == '__main__':
    print("\n" + "="*60)
    print("🖥️  ALGO TRADING WEBHOOK SERVER")
    print("="*60)
    print(f"   Mode:     PAPER TRADING")
    print(f"   Port:     8000")
    print(f"   Webhook:  POST /webhook")
    print(f"   Health:   GET  /health")
    print(f"   Indices:  {list(option_chain.INDEX_CONFIG.keys())}")
    print("="*60 + "\n")

    app.run(port=8000, debug=False)