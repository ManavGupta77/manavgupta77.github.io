"""

=======================================================================================
STRATEGY ALGORITHM: NIFTY IRON STRADDLE WITH DYNAMIC GAMMA ADJUSTMENTS
=======================================================================================
Instrument: NIFTY_INDEX Options
Lot Size: 65 (Strictly enforced for all PNL & impact calculations)
Time Gating: No logic or triggers are evaluated prior to exactly 09:30:00 AM.

--- 1. INITIAL ENTRY (09:30 AM) ---
* Find the Nifty Spot price exactly at 09:30 AM.
* Calculate the Anchor ATM Strike (Spot rounded to the nearest 50).
* Execute a 4-leg Iron Straddle:
    - Leg 1: SELL 1 Lot (65 qty) of ATM CE.
    - Leg 2: SELL 1 Lot (65 qty) of ATM PE.
    - Leg 3: BUY 1 Lot (65 qty) of OTM CE (ATM + 200 points) as Hedge.
    - Leg 4: BUY 1 Lot (65 qty) of OTM PE (ATM - 200 points) as Hedge.

--- 2. STOP LOSS LOGIC (Intraday) ---
* Condition: 30% Stop Loss is applied ONLY to the premium of the active SELL legs.
* Monitoring: Evaluated tick-by-tick (or minute-by-minute in backtest) strictly AFTER 09:30.

--- 3. ADJUSTMENT PROTOCOL (When a SL is Hit) ---
If the market trends and hits the SL on the "Tested" side (e.g., CE_SELL hits 30% loss):
    1. Close Tested Sell Leg: Square off the hit leg at Current Market Price (CMP) and realize the cash loss.
    2. Close Tested Hedge: Square off the corresponding Buy Hedge (e.g., CE_BUY) at CMP to free margin and stop decay.
    3. Double Untested Side (Real-World Accounting): Do NOT alter the original entry price of the surviving leg. 
       Instead, open a BRAND NEW "Adjustment Leg" (e.g., PE_SELL_ADJ) for 1 Lot (65 qty) at the Current Market Price. 
       (The portfolio now holds 2 lots of the untested side at two different entry prices).

--- 4. REVERSION PROTOCOL (The Spot Reset) ---
* Condition: If an Adjustment is currently active AND the Nifty Spot price returns to within a 3-point buffer of the ORIGINAL 09:30 Anchor ATM Strike.
    1. Close the Adjustment Leg: Square off ONLY the newly added "ADJ" leg at CMP to reset the untested side back to 1 lot.
    2. Re-open Straddle Leg: Sell 1 Lot of the previously stopped-out strike at CMP. Set a NEW 30% SL based on this new premium.
    3. Re-open Hedge: Buy 1 Lot of the previously closed hedge at CMP.
* Status: The strategy is now completely reset to a neutral Iron Straddle.

--- 5. FINAL SQUARE-OFF (15:29 PM) ---
* At exactly 15:29 PM, loop through the portfolio inventory.
* Square off ALL active legs (Originals, Hedges, and any active ADJ legs) at CMP.
* Calculate final Gross Realized PNL for the day.
=======================================================================================
"""


import pandas as pd
from trading_records.db_connector import db

# ==========================================
# CONFIGURATION
# ==========================================
DATE = "2026-02-16"
EXPIRY_DATE = "2026-02-17"
SYMBOL_SPOT = "NIFTY_INDEX"
SL_PCT = 0.30
LOT_SIZE = 65 

def run_backtest():
    db.connect()
    print(f"\n{'='*80}\n🚀 REAL-WORLD EXECUTION AUDIT: {DATE}\n{'='*80}")

    # 1. ANCHOR SPOT (Exactly at 09:30)
    query_spot = "SELECT timestamp, close FROM market_data WHERE symbol=? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1"
    spot_res = db.query_one(query_spot, [SYMBOL_SPOT, f"{DATE} 09:30:00"])
    if not spot_res: return print("❌ Error: Spot data missing.")
    
    spot_entry = spot_res['close']
    atm_strike = int(round(spot_entry / 50) * 50)
    entry_ts_iso = spot_res['timestamp'].replace(' ', 'T') + "+05:30"
    print(f"📍 ANCHOR: Time: {spot_res['timestamp']} | Spot: {spot_entry:.2f} | Strike: {atm_strike}\n")

    # 2. INITIAL ENTRY
    strikes = [atm_strike, atm_strike + 200, atm_strike - 200]
    query_options = f"SELECT tradingsymbol, strike, option_type, close FROM options_ohlc WHERE expiry = ? AND strike IN ({','.join(['?']*len(strikes))}) AND timestamp = ?"
    entry_data = db.query(query_options, [EXPIRY_DATE] + strikes + [entry_ts_iso])
    
    if not entry_data: return print(f"❌ Error: Options data missing for {entry_ts_iso}")

    legs = {}
    print(f"{'LEG ROLE':<14} | {'SYMBOL':<22} | {'ENTRY PR':<8} | {'QTY':<5} | {'SL TRIGGER'}")
    print("-" * 75)
    for row in entry_data:
        t, s, pr = row['option_type'], int(row['strike']), row['close']
        if s == atm_strike: key = f"{t}_SELL"
        elif s == atm_strike + 200 and t == 'CE': key = "CE_BUY"
        elif s == atm_strike - 200 and t == 'PE': key = "PE_BUY"
        else: continue
        
        sl_price = pr * (1 + SL_PCT) if 'SELL' in key else 9999
        legs[key] = {
            'sym': row['tradingsymbol'], 'strike': s, 'type': t, 
            'entry': pr, 'sl': sl_price, 'qty': -LOT_SIZE if 'SELL' in key else LOT_SIZE, 
            'active': True
        }
        print(f"{key:<14} | {row['tradingsymbol']:<22} | {pr:<8.2f} | {legs[key]['qty']:<5} | {sl_price if sl_price != 9999 else 'N/A'}")

    # 3. FETCH SIMULATION DATA (Strict Time-Gate)
    sym_list = [l['sym'] for l in legs.values()]
    query_sim = f"SELECT timestamp, tradingsymbol, close FROM options_ohlc WHERE tradingsymbol IN ({','.join(['?']*len(sym_list))}) AND timestamp >= ?"
    all_opt_data = pd.DataFrame(db.query(query_sim, sym_list + [entry_ts_iso]))
    price_df = all_opt_data.pivot(index='timestamp', columns='tradingsymbol', values='close').sort_index().ffill()
    
    # STATE VARIABLES
    extra_active = False
    tested_side = None
    realized_pnl = 0

    print(f"\n{'='*80}\n⏳ INTRADAY EVENTS LOG (REAL-WORLD ACCOUNTING)\n{'='*80}")
    print(f"{'TIME':<6} | {'SPOT':<8} | {'ACTION TAKEN':<24} | {'EXEC PRICE':<10} | {'CASH IMPACT'}")
    print("-" * 80)

    for ts, prices in price_df.iterrows():
        spot_ts = ts.replace('T', ' ').split('+')[0]
        spot_row = db.query_one("SELECT close FROM market_data WHERE timestamp=?", [spot_ts])
        if not spot_row: continue
        spot_curr = spot_row['close']
        time_str = ts[11:16] 

        # --- STEP 3 & 4: STOP LOSS ---
        if not extra_active:
            for side in ['CE_SELL', 'PE_SELL']:
                if legs[side]['active'] and prices[legs[side]['sym']] >= legs[side]['sl']:
                    
                    # 1. Close Hit Leg
                    exit_pr = prices[legs[side]['sym']]
                    loss_pts = legs[side]['entry'] - exit_pr
                    cash_loss = loss_pts * abs(legs[side]['qty'])
                    realized_pnl += cash_loss
                    legs[side]['active'] = False
                    print(f"{time_str:<6} | {spot_curr:<8.1f} | 🛑 CLOSE SL: {side:<11} | {exit_pr:<10.2f} | {int(cash_loss)}")

                    # 2. Close Corresponding Hedge
                    hedge_side = f"{side.split('_')[0]}_BUY"
                    hedge_exit_pr = prices[legs[hedge_side]['sym']]
                    hedge_pnl = (hedge_exit_pr - legs[hedge_side]['entry']) * legs[hedge_side]['qty']
                    realized_pnl += hedge_pnl
                    legs[hedge_side]['active'] = False
                    print(f"{time_str:<6} | {'':<8} | 🛡️ CLOSE HEDGE: {hedge_side:<8} | {hedge_exit_pr:<10.2f} | {int(hedge_pnl)}")

                    # 3. Open NEW Adjustment Leg (Real-World)
                    tested_side = side.split('_')[0]
                    untested_side = 'PE_SELL' if tested_side == 'CE' else 'CE_SELL'
                    adj_leg_key = f"{untested_side}_ADJ"
                    
                    current_untested_pr = prices[legs[untested_side]['sym']]
                    legs[adj_leg_key] = {
                        'sym': legs[untested_side]['sym'], 'strike': legs[untested_side]['strike'], 
                        'type': legs[untested_side]['type'], 'entry': current_untested_pr, 
                        'sl': 9999, 'qty': -LOT_SIZE, 'active': True
                    }
                    extra_active = True
                    print(f"{time_str:<6} | {'':<8} | ⚖️ OPEN NEW LEG: {adj_leg_key:<7} | {current_untested_pr:<10.2f} | ---")
                    break 

        # --- STEP 5: REVERSION ---
        elif extra_active and abs(spot_curr - atm_strike) <= 3:
            untested_side = 'PE_SELL' if tested_side == 'CE' else 'CE_SELL'
            adj_leg_key = f"{untested_side}_ADJ"
            
            # 1. Square-off the extra adjustment leg
            adj_exit_pr = prices[legs[adj_leg_key]['sym']]
            extra_lot_pnl = (legs[adj_leg_key]['entry'] - adj_exit_pr) * abs(legs[adj_leg_key]['qty'])
            realized_pnl += extra_lot_pnl
            legs[adj_leg_key]['active'] = False
            print(f"{time_str:<6} | {spot_curr:<8.1f} | 📉 CLOSE ADJ LEG: {adj_leg_key:<6} | {adj_exit_pr:<10.2f} | {int(extra_lot_pnl)}")

            # 2. Re-enter Original Straddle Leg
            re_side = f"{tested_side}_SELL"
            re_entry_pr = prices[legs[re_side]['sym']]
            legs[re_side]['entry'] = re_entry_pr
            legs[re_side]['sl'] = re_entry_pr * (1 + SL_PCT)
            legs[re_side]['active'] = True
            print(f"{time_str:<6} | {'':<8} | 🔄 RE-OPEN STRADDLE: {re_side:<3} | {re_entry_pr:<10.2f} | ---")

            # 3. Re-enter Hedge
            hedge_side = f"{tested_side}_BUY"
            legs[hedge_side]['entry'] = prices[legs[hedge_side]['sym']]
            legs[hedge_side]['active'] = True
            print(f"{time_str:<6} | {'':<8} | 🔄 RE-OPEN HEDGE: {hedge_side:<6} | {legs[hedge_side]['entry']:<10.2f} | ---")
            
            extra_active = False

    # --- FINAL EXIT ---
    print(f"\n{'='*80}\n🏁 FINAL PORTFOLIO SQUARE-OFF (15:29)\n{'='*80}")
    print(f"{'LEG ROLE':<14} | {'BASE PR':<8} | {'EXIT PR':<8} | {'STATUS':<14} | {'PNL (₹)'}")
    print("-" * 80)
    
    final_prices = price_df.iloc[-1]
    for k, leg in legs.items():
        if leg['active']:
            exit_pr = final_prices[leg['sym']]
            if 'SELL' in k or 'ADJ' in k:
                pnl = (leg['entry'] - exit_pr) * abs(leg['qty'])
            else:
                pnl = (exit_pr - leg['entry']) * abs(leg['qty'])
            realized_pnl += pnl
            print(f"{k:<14} | {leg['entry']:<8.2f} | {exit_pr:<8.2f} | {'ACTIVE':<14} | {int(pnl)}")
        else:
            print(f"{k:<14} | {leg['entry']:<8.2f} | {'-':<8} | {'CLOSED EARLY':<14} | 0")

    print(f"\n{'='*40}")
    print(f"💰 FINAL NET REALIZED PNL: ₹{realized_pnl:,.2f}")
    print(f"{'='*40}\n")
    db.close()

if __name__ == "__main__":
    run_backtest()