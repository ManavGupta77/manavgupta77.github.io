"""
=======================================================================================
STRATEGY: NIFTY IRON STRADDLE WITH DYNAMIC GAMMA ADJUSTMENTS — v2.0
=======================================================================================
Instrument:     NIFTY_INDEX Options
Lot Size:       65 (strictly enforced)
Time Gating:    No logic evaluated before 09:30:00 AM IST

STATE MACHINE:
  NEUTRAL     → Full iron straddle active (CE_SELL + PE_SELL + CE_BUY + PE_BUY)
  ADJUSTED    → One side SL'd, extra lot + hedge added on untested side
  FLIPPED     → Adjustment leg also SL'd, flipped back to originally-tested side
  ALL_OUT     → Both sides SL'd simultaneously. Wait for re-entry at original cost.
  DONE        → Final exit or no re-entry possible before cutoff.

ADJUSTMENT PROTOCOL (Req 4):
  When tested side SL hits:
    1. Exit tested SELL + tested BUY (hedge)
    2. On untested side: BUY hedge first (200 pts from original ATM), then SELL 1 lot at original ATM
    3. 30% SL on new adj sell leg (based on new entry premium)
    4. Buy legs always execute before sell legs

FLIP-BACK (Req 4 Q4 & G3):
  If adjustment sell leg also hits SL:
    1. Exit adj SELL + adj BUY on untested side
    2. Re-enter originally-tested side: SELL at original ATM + BUY hedge 200 pts away
    3. 30% SL on re-entered leg

REVERSION (Req 5-6):
  If spot returns within 15 points of original ATM while adjusted:
    1. Exit adj SELL + adj BUY on untested side
    2. Re-enter tested side: SELL at original ATM + BUY hedge 200 pts away
    3. 30% SL on re-entered leg

BOTH SIDES SL (G1):
  Exit everything. Watch premiums at original ATM strike.
  Re-enter full straddle + hedges when BOTH CE and PE premiums return to
  their original entry prices (or lower). Fresh 30% SL on re-entry.

CYCLES (G2): Unlimited until final exit time.
EXPIRY DAY (G5): No special behavior.
EXIT (Req 7): Final square-off between 15:15 - 15:25 (use 15:20 as target).
=======================================================================================
"""

import os
import sys
import pandas as pd
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from trading_records.db_connector import db

# ==========================================
# CONFIGURATION
# ==========================================
DATE = "2026-02-11"
EXPIRY_DATE = "2026-02-17"
SYMBOL_SPOT = "NIFTY_INDEX"
LOT_SIZE = 65
SL_PCT = 0.30               # 30% stop loss on individual sell premium
HEDGE_OFFSET = 200           # Points away for hedge
REVERSION_BUFFER = 15        # Points from ATM to trigger reversion
ENTRY_TIME = "09:30"         # Entry time (HH:MM)
EXIT_TIME = "15:20"          # Final exit time (HH:MM)
STRIKE_STEP = 50             # Nifty strike distance


class State(Enum):
    NEUTRAL = "NEUTRAL"
    ADJUSTED = "ADJUSTED"
    FLIPPED = "FLIPPED"
    ALL_OUT = "ALL_OUT"
    DONE = "DONE"


@dataclass
class Leg:
    """Represents a single option leg in the portfolio."""
    key: str
    symbol: str
    strike: int
    opt_type: str
    entry_price: float
    qty: int
    sl_price: float
    active: bool = True
    entry_time: str = ""
    exit_price: float = 0.0
    exit_time: str = ""
    realized_pnl: float = 0.0

    @property
    def is_sell(self) -> bool:
        return self.qty < 0

    def compute_pnl(self, exit_pr: float) -> float:
        if self.is_sell:
            return (self.entry_price - exit_pr) * abs(self.qty)
        else:
            return (exit_pr - self.entry_price) * abs(self.qty)


class Portfolio:
    def __init__(self):
        self.legs: dict[str, Leg] = {}
        self.closed_legs: list[Leg] = []
        self.total_realized_pnl: float = 0.0

    def add_leg(self, leg: Leg):
        self.legs[leg.key] = leg

    def close_leg(self, key: str, exit_price: float, exit_time: str) -> float:
        leg = self.legs[key]
        pnl = leg.compute_pnl(exit_price)
        leg.exit_price = exit_price
        leg.exit_time = exit_time
        leg.realized_pnl = pnl
        leg.active = False
        self.total_realized_pnl += pnl
        self.closed_legs.append(leg)
        del self.legs[key]
        return pnl

    def has_leg(self, key: str) -> bool:
        return key in self.legs and self.legs[key].active

    def active_keys(self) -> list[str]:
        return [k for k, v in self.legs.items() if v.active]


class EventLog:
    def __init__(self):
        self.events: list[dict] = []

    def log(self, time_str: str, spot: float, action: str, symbol: str = "",
            price: float = 0.0, pnl: Optional[float] = None, state: str = ""):
        self.events.append({
            'time': time_str, 'spot': spot, 'action': action,
            'symbol': symbol, 'price': price, 'pnl': pnl, 'state': state
        })

    def print_all(self):
        print(f"\n{'='*100}")
        print(f"{'TIME':<6} | {'SPOT':<9} | {'ACTION':<35} | {'SYMBOL':<24} | {'PRICE':<10} | {'PNL':>10} | STATE")
        print("-" * 100)
        for e in self.events:
            pnl_str = f"Rs.{int(e['pnl']):,}" if e['pnl'] is not None else ""
            spot_str = f"{e['spot']:.1f}" if e['spot'] else ""
            price_str = f"{e['price']:.2f}" if e['price'] else ""
            print(f"{e['time']:<6} | {spot_str:<9} | {e['action']:<35} | {e['symbol']:<24} | "
                  f"{price_str:<10} | {pnl_str:>10} | {e['state']}")


# ==========================================
# DATA LOADING
# ==========================================

def load_spot_data(date: str) -> pd.DataFrame:
    query = """
        SELECT timestamp, close
        FROM market_data
        WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
    """
    rows = db.query(query, [SYMBOL_SPOT, f"{date}T09:15:00+05:30", f"{date}T15:30:00+05:30"])
    if not rows:
        raise ValueError(f"No spot data for {date}")
    df = pd.DataFrame(rows)
    df['time'] = df['timestamp'].apply(lambda t: t[11:16])
    df = df.set_index('time')
    return df


def load_option_chain(date: str, expiry: str, strikes: list[int]) -> pd.DataFrame:
    placeholders = ','.join(['?'] * len(strikes))
    query = f"""
        SELECT timestamp, tradingsymbol, strike, option_type, close
        FROM options_ohlc
        WHERE expiry = ?
          AND strike IN ({placeholders})
          AND timestamp >= ?
          AND timestamp <= ?
        ORDER BY timestamp ASC
    """
    ts_start = f"{date}T09:15:00+05:30"
    ts_end = f"{date}T15:30:00+05:30"
    rows = db.query(query, [expiry] + [float(s) for s in strikes] + [ts_start, ts_end])
    if not rows:
        raise ValueError(f"No options data for {date}, expiry {expiry}, strikes {strikes}")
    df = pd.DataFrame(rows)
    meta = {}
    for _, r in df.drop_duplicates('tradingsymbol').iterrows():
        meta[r['tradingsymbol']] = {'strike': int(r['strike']), 'option_type': r['option_type']}
    price_df = df.pivot_table(index='timestamp', columns='tradingsymbol', values='close', aggfunc='last')
    price_df = price_df.sort_index().ffill()
    price_df['_time'] = price_df.index.map(lambda t: t[11:16])
    return price_df, meta


def find_symbol(meta: dict, strike: int, opt_type: str) -> Optional[str]:
    for sym, info in meta.items():
        if info['strike'] == strike and info['option_type'] == opt_type:
            return sym
    return None


def get_price(price_row, symbol: str) -> float:
    try:
        val = price_row[symbol]
        return float(val) if pd.notna(val) else 0.0
    except (KeyError, TypeError):
        return 0.0


# ==========================================
# PDF REPORT GENERATOR
# ==========================================

def generate_pdf_report(date, expiry_date, entry_spot, atm_strike, portfolio,
                         elog, adjustment_cycle, g1_active, total_pnl):
    """Auto-generate a professional PDF backtest report using reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         Table, TableStyle, HRFlowable)
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from datetime import datetime

        os.makedirs("reports", exist_ok=True)
        pdf_path = f"reports/iron_straddle_{date}.pdf"

        doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                                 leftMargin=1.5*cm, rightMargin=1.5*cm,
                                 topMargin=1.5*cm, bottomMargin=1.5*cm)

        styles = getSampleStyleSheet()
        story = []

        # ── Colour palette ──
        DARK_BLUE  = colors.HexColor('#1a237e')
        MID_BLUE   = colors.HexColor('#283593')
        LIGHT_BLUE = colors.HexColor('#e8eaf6')
        GREEN      = colors.HexColor('#2e7d32')
        RED        = colors.HexColor('#c62828')
        GREY_BG    = colors.HexColor('#f5f5f5')
        WHITE      = colors.white

        # ── Custom styles ──
        title_style = ParagraphStyle('Title', parent=styles['Normal'],
                                      fontSize=18, textColor=WHITE,
                                      alignment=TA_CENTER, fontName='Helvetica-Bold',
                                      spaceAfter=4)
        subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'],
                                         fontSize=10, textColor=colors.HexColor('#bbdefb'),
                                         alignment=TA_CENTER, fontName='Helvetica')
        section_style = ParagraphStyle('Section', parent=styles['Normal'],
                                        fontSize=11, textColor=WHITE,
                                        fontName='Helvetica-Bold', spaceAfter=0)
        body_style = ParagraphStyle('Body', parent=styles['Normal'],
                                     fontSize=9, textColor=colors.HexColor('#212121'),
                                     fontName='Helvetica', leading=14)
        label_style = ParagraphStyle('Label', parent=styles['Normal'],
                                      fontSize=8, textColor=colors.HexColor('#616161'),
                                      fontName='Helvetica')
        value_style = ParagraphStyle('Value', parent=styles['Normal'],
                                      fontSize=12, textColor=DARK_BLUE,
                                      fontName='Helvetica-Bold', alignment=TA_CENTER)

        pnl_color = GREEN if total_pnl >= 0 else RED
        pnl_style = ParagraphStyle('PNL', parent=styles['Normal'],
                                    fontSize=22, textColor=pnl_color,
                                    fontName='Helvetica-Bold', alignment=TA_CENTER)

        def section_header(title):
            tbl = Table([[Paragraph(title, section_style)]], colWidths=[17.7*cm])
            tbl.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), MID_BLUE),
                ('ROUNDEDCORNERS', [4]),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ('LEFTPADDING', (0,0), (-1,-1), 10),
            ]))
            return tbl

        # ══════════════════════════════════════════
        # HEADER BANNER
        # ══════════════════════════════════════════
        header_data = [[
            Paragraph('IRON STRADDLE BACKTEST REPORT v2.0', title_style),
        ]]
        header_tbl = Table(header_data, colWidths=[17.7*cm])
        header_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), DARK_BLUE),
            ('TOPPADDING', (0,0), (-1,-1), 14),
            ('BOTTOMPADDING', (0,0), (-1,-1), 14),
            ('ROUNDEDCORNERS', [6]),
        ]))
        story.append(header_tbl)

        sub_data = [[Paragraph(f'Trade Date: {date}   |   Expiry: {expiry_date}   |   Generated: {datetime.now().strftime("%d %b %Y %H:%M")}', subtitle_style)]]
        sub_tbl = Table(sub_data, colWidths=[17.7*cm])
        sub_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), MID_BLUE),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(sub_tbl)
        story.append(Spacer(1, 14))

        # ══════════════════════════════════════════
        # PNL SUMMARY BOX
        # ══════════════════════════════════════════
        pnl_sign = '+' if total_pnl >= 0 else ''
        pnl_data = [[
            Paragraph('NET P&L', ParagraphStyle('lbl', parent=label_style, alignment=TA_CENTER, fontSize=9)),
            Paragraph('ADJUSTMENT CYCLES', ParagraphStyle('lbl', parent=label_style, alignment=TA_CENTER, fontSize=9)),
            Paragraph('TOTAL LEGS', ParagraphStyle('lbl', parent=label_style, alignment=TA_CENTER, fontSize=9)),
            Paragraph('G1 EVENT', ParagraphStyle('lbl', parent=label_style, alignment=TA_CENTER, fontSize=9)),
        ],[
            Paragraph(f'Rs. {pnl_sign}{total_pnl:,.2f}', pnl_style),
            Paragraph(str(adjustment_cycle), value_style),
            Paragraph(str(len(portfolio.closed_legs)), value_style),
            Paragraph('YES' if g1_active else 'NO',
                      ParagraphStyle('g1', parent=value_style,
                                     textColor=RED if g1_active else GREEN)),
        ]]
        pnl_tbl = Table(pnl_data, colWidths=[5*cm, 4*cm, 4*cm, 4.7*cm])
        pnl_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), GREY_BG),
            ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#fff8e1')),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#e0e0e0')),
            ('LINEAFTER', (0,0), (2,-1), 0.5, colors.HexColor('#e0e0e0')),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(pnl_tbl)
        story.append(Spacer(1, 14))

        # ══════════════════════════════════════════
        # CONFIGURATION
        # ══════════════════════════════════════════
        story.append(section_header('  STRATEGY CONFIGURATION'))
        story.append(Spacer(1, 6))
        cfg_data = [
            ['Parameter', 'Value', 'Parameter', 'Value'],
            ['Instrument', 'NIFTY Index Options', 'Stop Loss', f'{int(SL_PCT*100)}% per sell leg'],
            ['Lot Size', str(LOT_SIZE), 'Hedge Offset', f'{HEDGE_OFFSET} points'],
            ['Entry Time', ENTRY_TIME, 'Reversion Buffer', f'{REVERSION_BUFFER} points'],
            ['Exit Time', EXIT_TIME, 'Strike Step', f'{STRIKE_STEP} points'],
            ['ATM Strike', str(atm_strike), 'Entry Spot', f'{entry_spot:.2f}'],
        ]
        cfg_tbl = Table(cfg_data, colWidths=[4*cm, 4.85*cm, 4*cm, 4.85*cm])
        cfg_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), LIGHT_BLUE),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8.5),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GREY_BG]),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#bdbdbd')),
            ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e0e0e0')),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(cfg_tbl)
        story.append(Spacer(1, 14))

        # ══════════════════════════════════════════
        # TRADE SUMMARY TABLE
        # ══════════════════════════════════════════
        story.append(section_header('  TRADE SUMMARY'))
        story.append(Spacer(1, 6))

        trade_data = [['Leg', 'Symbol', 'Entry Rs.', 'Exit Rs.', 'Qty', 'P&L Rs.', 'Entry', 'Exit']]
        for leg in portfolio.closed_legs:
            pnl_val = int(leg.realized_pnl)
            pnl_str = f'+{pnl_val:,}' if pnl_val >= 0 else f'{pnl_val:,}'
            trade_data.append([
                leg.key,
                leg.symbol,
                f'{leg.entry_price:.2f}',
                f'{leg.exit_price:.2f}',
                str(leg.qty),
                pnl_str,
                leg.entry_time,
                leg.exit_time,
            ])

        col_w = [3*cm, 4.5*cm, 2.1*cm, 2.1*cm, 1.5*cm, 2.2*cm, 1.5*cm, 1.5*cm]
        trade_tbl = Table(trade_data, colWidths=col_w)

        # Build row colours and PNL colouring
        trade_style = [
            ('BACKGROUND', (0,0), (-1,0), LIGHT_BLUE),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GREY_BG]),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#bdbdbd')),
            ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e0e0e0')),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('ALIGN', (2,0), (-1,-1), 'RIGHT'),
        ]
        for i, leg in enumerate(portfolio.closed_legs, start=1):
            col = GREEN if leg.realized_pnl >= 0 else RED
            trade_style.append(('TEXTCOLOR', (5, i), (5, i), col))
            trade_style.append(('FONTNAME', (5, i), (5, i), 'Helvetica-Bold'))

        trade_tbl.setStyle(TableStyle(trade_style))
        story.append(trade_tbl)
        story.append(Spacer(1, 14))

        # ══════════════════════════════════════════
        # EVENT LOG
        # ══════════════════════════════════════════
        story.append(section_header('  EVENT LOG'))
        story.append(Spacer(1, 6))

        event_data = [['Time', 'Spot', 'Action', 'Symbol', 'Price', 'P&L', 'State']]
        for e in elog.events:
            pnl_str = f"Rs.{int(e['pnl']):,}" if e['pnl'] is not None else ''
            spot_str = f"{e['spot']:.1f}" if e['spot'] else ''
            price_str = f"{e['price']:.2f}" if e['price'] else ''
            # Strip emoji for PDF (avoid font issues)
            action = e['action'].encode('ascii', 'ignore').decode('ascii').strip()
            event_data.append([
                e['time'], spot_str, action,
                e['symbol'], price_str, pnl_str, e['state']
            ])

        ev_col_w = [1.2*cm, 1.8*cm, 5.5*cm, 4*cm, 1.8*cm, 2*cm, 1.4*cm]
        ev_tbl = Table(event_data, colWidths=ev_col_w)
        ev_style = [
            ('BACKGROUND', (0,0), (-1,0), LIGHT_BLUE),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 7),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GREY_BG]),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#bdbdbd')),
            ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e0e0e0')),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('LEFTPADDING', (0,0), (-1,-1), 4),
            ('WORDWRAP', (2,0), (2,-1), True),
        ]
        for i, e in enumerate(elog.events, start=1):
            if e['pnl'] is not None:
                col = GREEN if e['pnl'] >= 0 else RED
                ev_style.append(('TEXTCOLOR', (5, i), (5, i), col))
                ev_style.append(('FONTNAME', (5, i), (5, i), 'Helvetica-Bold'))

        ev_tbl.setStyle(TableStyle(ev_style))
        story.append(ev_tbl)
        story.append(Spacer(1, 14))

        # ══════════════════════════════════════════
        # FOOTER
        # ══════════════════════════════════════════
        footer_data = [[Paragraph(
            f'Generated by Iron Straddle Backtest Engine v2.0  |  '
            f'trading_infrastructure  |  {datetime.now().strftime("%d %b %Y %H:%M:%S")}',
            ParagraphStyle('footer', parent=label_style, alignment=TA_CENTER,
                           textColor=WHITE, fontSize=7.5)
        )]]
        footer_tbl = Table(footer_data, colWidths=[17.7*cm])
        footer_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), DARK_BLUE),
            ('TOPPADDING', (0,0), (-1,-1), 7),
            ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ]))
        story.append(footer_tbl)

        doc.build(story)
        return pdf_path

    except Exception as e:
        print(f"  WARNING: PDF generation failed: {e}")
        return None


# ==========================================
# STRATEGY ENGINE
# ==========================================

def run_backtest():
    db.connect()
    print(f"\n{'='*100}")
    print(f"IRON STRADDLE BACKTEST v2.0 -- {DATE}")
    print(f"{'='*100}")
    print(f"Config: SL={SL_PCT*100:.0f}% | Hedge={HEDGE_OFFSET}pts | Reversion={REVERSION_BUFFER}pts | "
          f"Lot={LOT_SIZE} | Entry={ENTRY_TIME} | Exit={EXIT_TIME}")

    spot_df = load_spot_data(DATE)
    entry_spot = spot_df.loc[ENTRY_TIME, 'close'] if ENTRY_TIME in spot_df.index else None
    if entry_spot is None:
        return print(f"No spot data at {ENTRY_TIME}")

    atm_strike = int(round(entry_spot / STRIKE_STEP) * STRIKE_STEP)
    ce_hedge_strike = atm_strike + HEDGE_OFFSET
    pe_hedge_strike = atm_strike - HEDGE_OFFSET

    print(f"\nANCHOR: Spot={entry_spot:.2f} | ATM={atm_strike} | "
          f"CE Hedge={ce_hedge_strike} | PE Hedge={pe_hedge_strike}")

    all_strikes = sorted(set([atm_strike, ce_hedge_strike, pe_hedge_strike]))
    price_df, meta = load_option_chain(DATE, EXPIRY_DATE, all_strikes)

    sym_ce_atm = find_symbol(meta, atm_strike, 'CE')
    sym_pe_atm = find_symbol(meta, atm_strike, 'PE')
    sym_ce_hedge = find_symbol(meta, ce_hedge_strike, 'CE')
    sym_pe_hedge = find_symbol(meta, pe_hedge_strike, 'PE')

    for name, sym in [('CE ATM', sym_ce_atm), ('PE ATM', sym_pe_atm),
                      ('CE Hedge', sym_ce_hedge), ('PE Hedge', sym_pe_hedge)]:
        if not sym:
            return print(f"Symbol not found: {name}")

    entry_ts_iso = f"{DATE}T{ENTRY_TIME}:00+05:30"
    if entry_ts_iso not in price_df.index:
        valid_ts = [t for t in price_df.index if t >= entry_ts_iso]
        if not valid_ts:
            return print(f"No option data at or after {entry_ts_iso}")
        entry_ts_iso = valid_ts[0]
        print(f"Adjusted entry timestamp to {entry_ts_iso}")

    entry_prices = price_df.loc[entry_ts_iso]
    portfolio = Portfolio()
    elog = EventLog()

    ce_sell_pr = get_price(entry_prices, sym_ce_atm)
    pe_sell_pr = get_price(entry_prices, sym_pe_atm)
    ce_buy_pr = get_price(entry_prices, sym_ce_hedge)
    pe_buy_pr = get_price(entry_prices, sym_pe_hedge)

    original_ce_sell_premium = ce_sell_pr
    original_pe_sell_premium = pe_sell_pr

    portfolio.add_leg(Leg("CE_SELL", sym_ce_atm, atm_strike, 'CE', ce_sell_pr, -LOT_SIZE,
                          ce_sell_pr * (1 + SL_PCT), entry_time=ENTRY_TIME))
    portfolio.add_leg(Leg("PE_SELL", sym_pe_atm, atm_strike, 'PE', pe_sell_pr, -LOT_SIZE,
                          pe_sell_pr * (1 + SL_PCT), entry_time=ENTRY_TIME))
    portfolio.add_leg(Leg("CE_BUY", sym_ce_hedge, ce_hedge_strike, 'CE', ce_buy_pr, LOT_SIZE,
                          9999, entry_time=ENTRY_TIME))
    portfolio.add_leg(Leg("PE_BUY", sym_pe_hedge, pe_hedge_strike, 'PE', pe_buy_pr, LOT_SIZE,
                          9999, entry_time=ENTRY_TIME))

    combined_premium = ce_sell_pr + pe_sell_pr
    net_credit = (ce_sell_pr + pe_sell_pr - ce_buy_pr - pe_buy_pr) * LOT_SIZE

    print(f"\n{'─'*80}")
    print(f"{'LEG':<12} | {'SYMBOL':<24} | {'STRIKE':<7} | {'ENTRY Rs.':<9} | {'SL Rs.':<9} | QTY")
    print(f"{'─'*80}")
    for k, leg in portfolio.legs.items():
        sl_str = f"{leg.sl_price:.2f}" if leg.sl_price < 9999 else "N/A"
        print(f"{k:<12} | {leg.symbol:<24} | {leg.strike:<7} | {leg.entry_price:<9.2f} | {sl_str:<9} | {leg.qty}")
    print(f"{'─'*80}")
    print(f"Combined Premium: Rs.{combined_premium:.2f} | Net Credit: Rs.{net_credit:,.0f}")

    elog.log(ENTRY_TIME, entry_spot, "ENTRY: Iron Straddle opened", state="NEUTRAL")

    state = State.NEUTRAL
    tested_side: Optional[str] = None
    adjustment_cycle = 0
    g1_active = False

    exit_ts_iso = f"{DATE}T{EXIT_TIME}:00+05:30"
    sim_df = price_df[(price_df.index > entry_ts_iso) & (price_df.index <= exit_ts_iso)].copy()

    for ts, prices in sim_df.iterrows():
        time_str = ts[11:16]
        if time_str in spot_df.index:
            spot_curr = spot_df.loc[time_str, 'close']
        else:
            continue

        if state == State.DONE:
            break

        if state == State.ALL_OUT:
            ce_curr = get_price(prices, sym_ce_atm)
            pe_curr = get_price(prices, sym_pe_atm)
            if ce_curr <= original_ce_sell_premium and pe_curr <= original_pe_sell_premium:
                ce_hedge_pr = get_price(prices, sym_ce_hedge)
                pe_hedge_pr = get_price(prices, sym_pe_hedge)
                portfolio.add_leg(Leg("CE_BUY", sym_ce_hedge, ce_hedge_strike, 'CE',
                                      ce_hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                portfolio.add_leg(Leg("PE_BUY", sym_pe_hedge, pe_hedge_strike, 'PE',
                                      pe_hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                elog.log(time_str, spot_curr, "G1 RE-ENTRY: Buy CE hedge",
                         sym_ce_hedge, ce_hedge_pr, state="ALL_OUT->NEUTRAL")
                elog.log(time_str, spot_curr, "G1 RE-ENTRY: Buy PE hedge", sym_pe_hedge, pe_hedge_pr)
                portfolio.add_leg(Leg("CE_SELL", sym_ce_atm, atm_strike, 'CE',
                                      ce_curr, -LOT_SIZE, ce_curr * (1 + SL_PCT), entry_time=time_str))
                portfolio.add_leg(Leg("PE_SELL", sym_pe_atm, atm_strike, 'PE',
                                      pe_curr, -LOT_SIZE, pe_curr * (1 + SL_PCT), entry_time=time_str))
                elog.log(time_str, spot_curr, "G1 RE-ENTRY: Sell CE ATM", sym_ce_atm, ce_curr)
                elog.log(time_str, spot_curr, "G1 RE-ENTRY: Sell PE ATM", sym_pe_atm, pe_curr)
                state = State.NEUTRAL
                tested_side = None
                g1_active = False
                adjustment_cycle += 1
                elog.log(time_str, spot_curr, f"G1 RE-ENTRY COMPLETE (cycle {adjustment_cycle})", state="NEUTRAL")
            continue

        if state == State.NEUTRAL:
            ce_sl_hit = False
            pe_sl_hit = False
            if portfolio.has_leg("CE_SELL"):
                ce_pr = get_price(prices, portfolio.legs["CE_SELL"].symbol)
                if ce_pr >= portfolio.legs["CE_SELL"].sl_price:
                    ce_sl_hit = True
            if portfolio.has_leg("PE_SELL"):
                pe_pr = get_price(prices, portfolio.legs["PE_SELL"].symbol)
                if pe_pr >= portfolio.legs["PE_SELL"].sl_price:
                    pe_sl_hit = True

            if ce_sl_hit and pe_sl_hit:
                elog.log(time_str, spot_curr, "BOTH SIDES SL HIT", state="NEUTRAL->ALL_OUT")
                for key in list(portfolio.legs.keys()):
                    sym = portfolio.legs[key].symbol
                    exit_pr = get_price(prices, sym)
                    pnl = portfolio.close_leg(key, exit_pr, time_str)
                    elog.log(time_str, 0, f"EXIT: {key}", sym, exit_pr, pnl)
                state = State.ALL_OUT
                g1_active = True
                continue

            if ce_sl_hit or pe_sl_hit:
                if ce_sl_hit:
                    tested_side = 'CE'
                    tested_sell_key = "CE_SELL"
                    tested_buy_key = "CE_BUY"
                    adj_sell_key = "PE_SELL_ADJ"
                    adj_buy_key = "PE_BUY_ADJ"
                    adj_hedge_strike = atm_strike - HEDGE_OFFSET
                    adj_hedge_type = 'PE'
                else:
                    tested_side = 'PE'
                    tested_sell_key = "PE_SELL"
                    tested_buy_key = "PE_BUY"
                    adj_sell_key = "CE_SELL_ADJ"
                    adj_buy_key = "CE_BUY_ADJ"
                    adj_hedge_strike = atm_strike + HEDGE_OFFSET
                    adj_hedge_type = 'CE'

                adjustment_cycle += 1
                elog.log(time_str, spot_curr,
                         f"SL HIT: {tested_sell_key} (cycle {adjustment_cycle})",
                         state="NEUTRAL->ADJUSTED")

                exit_pr = get_price(prices, portfolio.legs[tested_sell_key].symbol)
                pnl = portfolio.close_leg(tested_sell_key, exit_pr, time_str)
                elog.log(time_str, 0, f"CLOSE tested sell: {tested_sell_key}",
                         portfolio.closed_legs[-1].symbol, exit_pr, pnl)

                if portfolio.has_leg(tested_buy_key):
                    exit_pr = get_price(prices, portfolio.legs[tested_buy_key].symbol)
                    pnl = portfolio.close_leg(tested_buy_key, exit_pr, time_str)
                    elog.log(time_str, 0, f"CLOSE tested hedge: {tested_buy_key}",
                             portfolio.closed_legs[-1].symbol, exit_pr, pnl)

                adj_hedge_sym = find_symbol(meta, adj_hedge_strike, adj_hedge_type)
                if adj_hedge_sym:
                    adj_hedge_pr = get_price(prices, adj_hedge_sym)
                    portfolio.add_leg(Leg(adj_buy_key, adj_hedge_sym, adj_hedge_strike,
                                          adj_hedge_type, adj_hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                    elog.log(time_str, 0, f"OPEN adj hedge: {adj_buy_key}", adj_hedge_sym, adj_hedge_pr)

                untested_type = 'PE' if tested_side == 'CE' else 'CE'
                adj_sell_sym = find_symbol(meta, atm_strike, untested_type)
                if adj_sell_sym:
                    adj_sell_pr = get_price(prices, adj_sell_sym)
                    portfolio.add_leg(Leg(adj_sell_key, adj_sell_sym, atm_strike,
                                          untested_type, adj_sell_pr, -LOT_SIZE,
                                          adj_sell_pr * (1 + SL_PCT), entry_time=time_str))
                    elog.log(time_str, 0, f"OPEN adj sell: {adj_sell_key}", adj_sell_sym, adj_sell_pr)

                state = State.ADJUSTED
                elog.log(time_str, spot_curr,
                         f"ADJUSTED: {tested_side} tested, extra lot on {'PE' if tested_side == 'CE' else 'CE'} side",
                         state="ADJUSTED")
                continue

        if state == State.ADJUSTED:
            untested_type = 'PE' if tested_side == 'CE' else 'CE'
            adj_sell_key = f"{untested_type}_SELL_ADJ"
            adj_buy_key = f"{untested_type}_BUY_ADJ"

            if abs(spot_curr - atm_strike) <= REVERSION_BUFFER:
                elog.log(time_str, spot_curr,
                         f"REVERSION: Spot within {REVERSION_BUFFER}pts of ATM",
                         state="ADJUSTED->NEUTRAL")
                if portfolio.has_leg(adj_sell_key):
                    exit_pr = get_price(prices, portfolio.legs[adj_sell_key].symbol)
                    pnl = portfolio.close_leg(adj_sell_key, exit_pr, time_str)
                    elog.log(time_str, 0, f"CLOSE adj sell: {adj_sell_key}",
                             portfolio.closed_legs[-1].symbol, exit_pr, pnl)
                if portfolio.has_leg(adj_buy_key):
                    exit_pr = get_price(prices, portfolio.legs[adj_buy_key].symbol)
                    pnl = portfolio.close_leg(adj_buy_key, exit_pr, time_str)
                    elog.log(time_str, 0, f"CLOSE adj hedge: {adj_buy_key}",
                             portfolio.closed_legs[-1].symbol, exit_pr, pnl)

                tested_sell_key = f"{tested_side}_SELL"
                tested_buy_key = f"{tested_side}_BUY"
                tested_sell_sym = find_symbol(meta, atm_strike, tested_side)
                tested_hedge_strike = (atm_strike + HEDGE_OFFSET if tested_side == 'CE'
                                       else atm_strike - HEDGE_OFFSET)
                tested_hedge_sym = find_symbol(meta, tested_hedge_strike, tested_side)
                if tested_hedge_sym:
                    hedge_pr = get_price(prices, tested_hedge_sym)
                    portfolio.add_leg(Leg(tested_buy_key, tested_hedge_sym, tested_hedge_strike,
                                          tested_side, hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                    elog.log(time_str, 0, f"RE-OPEN hedge: {tested_buy_key}", tested_hedge_sym, hedge_pr)
                if tested_sell_sym:
                    sell_pr = get_price(prices, tested_sell_sym)
                    portfolio.add_leg(Leg(tested_sell_key, tested_sell_sym, atm_strike,
                                          tested_side, sell_pr, -LOT_SIZE,
                                          sell_pr * (1 + SL_PCT), entry_time=time_str))
                    elog.log(time_str, 0, f"RE-OPEN sell: {tested_sell_key}", tested_sell_sym, sell_pr)

                state = State.NEUTRAL
                tested_side = None
                elog.log(time_str, spot_curr, "REVERSION COMPLETE -> NEUTRAL", state="NEUTRAL")
                continue

            if portfolio.has_leg(adj_sell_key):
                adj_leg = portfolio.legs[adj_sell_key]
                adj_pr = get_price(prices, adj_leg.symbol)
                if adj_pr >= adj_leg.sl_price:
                    elog.log(time_str, spot_curr,
                             f"ADJ SL HIT: {adj_sell_key} -> FLIP BACK",
                             state="ADJUSTED->FLIPPED")
                    pnl = portfolio.close_leg(adj_sell_key, adj_pr, time_str)
                    elog.log(time_str, 0, f"CLOSE adj sell: {adj_sell_key}",
                             portfolio.closed_legs[-1].symbol, adj_pr, pnl)
                    if portfolio.has_leg(adj_buy_key):
                        exit_pr = get_price(prices, portfolio.legs[adj_buy_key].symbol)
                        pnl = portfolio.close_leg(adj_buy_key, exit_pr, time_str)
                        elog.log(time_str, 0, f"CLOSE adj hedge: {adj_buy_key}",
                                 portfolio.closed_legs[-1].symbol, exit_pr, pnl)

                    flip_sell_key = f"{tested_side}_SELL"
                    flip_buy_key = f"{tested_side}_BUY"
                    flip_sell_sym = find_symbol(meta, atm_strike, tested_side)
                    flip_hedge_strike = (atm_strike + HEDGE_OFFSET if tested_side == 'CE'
                                         else atm_strike - HEDGE_OFFSET)
                    flip_hedge_sym = find_symbol(meta, flip_hedge_strike, tested_side)
                    if flip_hedge_sym:
                        hedge_pr = get_price(prices, flip_hedge_sym)
                        portfolio.add_leg(Leg(flip_buy_key, flip_hedge_sym, flip_hedge_strike,
                                              tested_side, hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                        elog.log(time_str, 0, f"FLIP: Open hedge {flip_buy_key}", flip_hedge_sym, hedge_pr)
                    if flip_sell_sym:
                        sell_pr = get_price(prices, flip_sell_sym)
                        portfolio.add_leg(Leg(flip_sell_key, flip_sell_sym, atm_strike,
                                              tested_side, sell_pr, -LOT_SIZE,
                                              sell_pr * (1 + SL_PCT), entry_time=time_str))
                        elog.log(time_str, 0, f"FLIP: Open sell {flip_sell_key}", flip_sell_sym, sell_pr)

                    state = State.FLIPPED
                    elog.log(time_str, spot_curr,
                             f"FLIPPED: Now on {tested_side} side + original {'PE' if tested_side == 'CE' else 'CE'}_SELL",
                             state="FLIPPED")
                    continue

            untested_sell_orig_key = f"{untested_type}_SELL"
            if portfolio.has_leg(untested_sell_orig_key):
                orig_leg = portfolio.legs[untested_sell_orig_key]
                orig_pr = get_price(prices, orig_leg.symbol)
                if orig_pr >= orig_leg.sl_price:
                    elog.log(time_str, spot_curr,
                             f"ORIGINAL {untested_sell_orig_key} ALSO SL'd -> ALL_OUT",
                             state="ADJUSTED->ALL_OUT")
                    for key in list(portfolio.legs.keys()):
                        sym = portfolio.legs[key].symbol
                        exit_pr = get_price(prices, sym)
                        pnl = portfolio.close_leg(key, exit_pr, time_str)
                        elog.log(time_str, 0, f"EXIT: {key}", sym, exit_pr, pnl)
                    state = State.ALL_OUT
                    g1_active = True
                    continue

        if state == State.FLIPPED:
            flipped_sell_key = f"{tested_side}_SELL"
            untested_type = 'PE' if tested_side == 'CE' else 'CE'
            untested_sell_key = f"{untested_type}_SELL"

            if abs(spot_curr - atm_strike) <= REVERSION_BUFFER:
                elog.log(time_str, spot_curr, "REVERSION (from FLIPPED)", state="FLIPPED->NEUTRAL")
                untested_hedge_strike = (atm_strike + HEDGE_OFFSET if untested_type == 'CE'
                                         else atm_strike - HEDGE_OFFSET)
                untested_hedge_key = f"{untested_type}_BUY"
                untested_hedge_sym = find_symbol(meta, untested_hedge_strike, untested_type)
                if not portfolio.has_leg(untested_hedge_key) and untested_hedge_sym:
                    hedge_pr = get_price(prices, untested_hedge_sym)
                    portfolio.add_leg(Leg(untested_hedge_key, untested_hedge_sym,
                                          untested_hedge_strike, untested_type,
                                          hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                    elog.log(time_str, 0, f"RESTORE hedge: {untested_hedge_key}", untested_hedge_sym, hedge_pr)
                if portfolio.has_leg(flipped_sell_key):
                    flipped_pr = get_price(prices, portfolio.legs[flipped_sell_key].symbol)
                    portfolio.legs[flipped_sell_key].sl_price = flipped_pr * (1 + SL_PCT)
                state = State.NEUTRAL
                tested_side = None
                elog.log(time_str, spot_curr, "FLIPPED -> NEUTRAL (full straddle restored)", state="NEUTRAL")
                continue

            if portfolio.has_leg(flipped_sell_key):
                flip_leg = portfolio.legs[flipped_sell_key]
                flip_pr = get_price(prices, flip_leg.symbol)
                if flip_pr >= flip_leg.sl_price:
                    elog.log(time_str, spot_curr, f"FLIPPED LEG SL: {flipped_sell_key}",
                             state="FLIPPED->ADJUSTED(reversed)")
                    pnl = portfolio.close_leg(flipped_sell_key, flip_pr, time_str)
                    elog.log(time_str, 0, f"CLOSE flipped sell: {flipped_sell_key}",
                             portfolio.closed_legs[-1].symbol, flip_pr, pnl)
                    flipped_buy_key = f"{tested_side}_BUY"
                    if portfolio.has_leg(flipped_buy_key):
                        exit_pr = get_price(prices, portfolio.legs[flipped_buy_key].symbol)
                        pnl = portfolio.close_leg(flipped_buy_key, exit_pr, time_str)
                        elog.log(time_str, 0, f"CLOSE flipped hedge: {flipped_buy_key}",
                                 portfolio.closed_legs[-1].symbol, exit_pr, pnl)

                    new_adj_side = tested_side
                    tested_side = untested_type
                    adj_sell_key = f"{new_adj_side}_SELL_ADJ"
                    adj_buy_key = f"{new_adj_side}_BUY_ADJ"
                    adj_hedge_strike = (atm_strike + HEDGE_OFFSET if new_adj_side == 'CE'
                                        else atm_strike - HEDGE_OFFSET)
                    adj_hedge_sym = find_symbol(meta, adj_hedge_strike, new_adj_side)
                    if adj_hedge_sym:
                        adj_hedge_pr = get_price(prices, adj_hedge_sym)
                        portfolio.add_leg(Leg(adj_buy_key, adj_hedge_sym, adj_hedge_strike,
                                              new_adj_side, adj_hedge_pr, LOT_SIZE, 9999, entry_time=time_str))
                        elog.log(time_str, 0, f"OPEN adj hedge: {adj_buy_key}", adj_hedge_sym, adj_hedge_pr)
                    adj_sell_sym = find_symbol(meta, atm_strike, new_adj_side)
                    if adj_sell_sym:
                        adj_sell_pr = get_price(prices, adj_sell_sym)
                        portfolio.add_leg(Leg(adj_sell_key, adj_sell_sym, atm_strike,
                                              new_adj_side, adj_sell_pr, -LOT_SIZE,
                                              adj_sell_pr * (1 + SL_PCT), entry_time=time_str))
                        elog.log(time_str, 0, f"OPEN adj sell: {adj_sell_key}", adj_sell_sym, adj_sell_pr)

                    adjustment_cycle += 1
                    state = State.ADJUSTED
                    elog.log(time_str, spot_curr,
                             f"RE-ADJUSTED (cycle {adjustment_cycle}): now {tested_side} tested, extra on {new_adj_side}",
                             state="ADJUSTED")
                    continue

            if portfolio.has_leg(untested_sell_key):
                orig_leg = portfolio.legs[untested_sell_key]
                orig_pr = get_price(prices, orig_leg.symbol)
                if orig_pr >= orig_leg.sl_price:
                    elog.log(time_str, spot_curr,
                             f"UNTESTED {untested_sell_key} SL'd in FLIPPED -> ALL_OUT",
                             state="FLIPPED->ALL_OUT")
                    for key in list(portfolio.legs.keys()):
                        sym = portfolio.legs[key].symbol
                        exit_pr = get_price(prices, sym)
                        pnl = portfolio.close_leg(key, exit_pr, time_str)
                        elog.log(time_str, 0, f"EXIT: {key}", sym, exit_pr, pnl)
                    state = State.ALL_OUT
                    g1_active = True
                    continue

    # ---------------------------------------------------
    # FINAL SQUARE-OFF
    # ---------------------------------------------------
    print(f"\n{'='*100}")
    print(f"FINAL SQUARE-OFF AT {EXIT_TIME}")
    print(f"{'='*100}")

    if len(sim_df) > 0:
        final_prices = sim_df.iloc[-1]
        final_time = sim_df.index[-1][11:16]
    else:
        final_prices = price_df.iloc[-1]
        final_time = "EOD"

    for key in list(portfolio.legs.keys()):
        leg = portfolio.legs[key]
        exit_pr = get_price(final_prices, leg.symbol)
        pnl = portfolio.close_leg(key, exit_pr, final_time)
        elog.log(final_time, 0, f"FINAL EXIT: {key}", leg.symbol, exit_pr, pnl, "DONE")

    state = State.DONE

    # ---------------------------------------------------
    # PRINT REPORT
    # ---------------------------------------------------
    elog.print_all()

    print(f"\n{'='*100}")
    print(f"TRADE SUMMARY")
    print(f"{'='*100}")
    print(f"{'LEG':<16} | {'ENTRY Rs.':<9} | {'EXIT Rs.':<9} | {'QTY':<6} | {'PNL Rs.':>10} | {'ENTRY':>6} | {'EXIT':>6}")
    print(f"{'─'*90}")

    for leg in portfolio.closed_legs:
        print(f"{leg.key:<16} | {leg.entry_price:<9.2f} | {leg.exit_price:<9.2f} | "
              f"{leg.qty:<6} | {int(leg.realized_pnl):>10,} | {leg.entry_time:>6} | {leg.exit_time:>6}")

    print(f"{'─'*90}")
    print(f"  Total Adjustment Cycles: {adjustment_cycle}")
    print(f"  Total Legs Traded:       {len(portfolio.closed_legs)}")
    print(f"  G1 (Both SL) Events:     {'Yes' if g1_active else 'No'}")
    print(f"\n{'='*50}")
    print(f"  FINAL NET REALIZED PNL: Rs.{portfolio.total_realized_pnl:,.2f}")
    print(f"{'='*50}\n")

    # ---------------------------------------------------
    # AUTO-GENERATE PDF REPORT
    # ---------------------------------------------------
    print("Generating PDF report...")
    pdf_path = generate_pdf_report(
        date=DATE,
        expiry_date=EXPIRY_DATE,
        entry_spot=entry_spot,
        atm_strike=atm_strike,
        portfolio=portfolio,
        elog=elog,
        adjustment_cycle=adjustment_cycle,
        g1_active=g1_active,
        total_pnl=portfolio.total_realized_pnl
    )
    if pdf_path:
        print(f"PDF Report saved: {pdf_path}")
    else:
        print("PDF generation skipped.")

    db.close()


if __name__ == "__main__":
    run_backtest()
