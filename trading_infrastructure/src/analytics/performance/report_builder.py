"""
===========================================================================
BACKTEST PDF REPORT GENERATOR — v3
===========================================================================
Rajat Gupta Proprietary Algo System

Reusable report class for any AlgoSystem backtest strategy.

Usage:
    from analytics.performance.report_builder import BacktestReport

    report = BacktestReport(
        strategy_name="Iron Straddle v2",
        date="2026-02-17",
        output_path="reports/iron_straddle_2026-02-17.pdf"
    )
    report.set_config({...})
    report.set_entry_summary(spot=25591, atm=25600, legs=[...])
    report.add_events([...])
    report.set_trade_summary(legs=[...], cycles=2, total_pnl=-1234)
    report.generate()
===========================================================================
"""

import os
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable
)


# ─────────────────────────────────────────────
# BRANDING
# ─────────────────────────────────────────────
BRAND_NAME = "Rajat Gupta Proprietary Algo System"
BRAND_SHORT = "RGPAS"


# ─────────────────────────────────────────────
# COLOR PALETTE
# ─────────────────────────────────────────────
class Colors:
    PRIMARY        = colors.HexColor("#0f1b2d")    # Deep navy — titles, strong text
    SECONDARY      = colors.HexColor("#1e3a5f")    # Slightly lighter navy
    ACCENT         = colors.HexColor("#2c6fbb")    # Blue accent — section headings
    ACCENT_LIGHT   = colors.HexColor("#e9f0f8")    # Light blue wash

    TH_BG          = colors.HexColor("#2c3e50")    # Table header background
    TH_TEXT         = colors.HexColor("#ecf0f1")    # Table header text — HIGH CONTRAST

    ROW_ALT        = colors.HexColor("#f7f9fb")    # Subtle alternating rows
    BORDER         = colors.HexColor("#dce1e8")     # Table borders
    MUTED          = colors.HexColor("#7b8a9c")     # Secondary text

    PROFIT         = colors.HexColor("#117a3e")
    LOSS           = colors.HexColor("#c0392b")

    TAG_GREEN      = colors.HexColor("#e8f5e9")
    TAG_RED        = colors.HexColor("#fdecea")
    TAG_BLUE       = colors.HexColor("#e3eef9")
    TAG_AMBER      = colors.HexColor("#fff8e1")
    TAG_GREY       = colors.HexColor("#eceff1")

    BRAND_BAR      = colors.HexColor("#0f1b2d")    # Top/bottom brand bar
    BRAND_TEXT     = colors.HexColor("#8fa4bf")     # Muted brand text


# ─────────────────────────────────────────────
# STYLES (cached singleton)
# ─────────────────────────────────────────────
_CACHE = None

def _S():
    global _CACHE
    if _CACHE:
        return _CACHE

    base = getSampleStyleSheet()
    s = {'base': base}

    s['brand'] = ParagraphStyle(
        '_brand', fontSize=7.5, leading=10, fontName='Helvetica',
        textColor=Colors.BRAND_TEXT, alignment=TA_LEFT
    )
    s['title'] = ParagraphStyle(
        '_title', fontSize=16, leading=21, fontName='Helvetica-Bold',
        textColor=Colors.PRIMARY, spaceAfter=1
    )
    s['subtitle'] = ParagraphStyle(
        '_sub', fontSize=8.5, leading=12, fontName='Helvetica',
        textColor=Colors.MUTED, spaceAfter=10
    )
    s['section'] = ParagraphStyle(
        '_sec', fontSize=11, leading=15, fontName='Helvetica-Bold',
        textColor=Colors.ACCENT, spaceBefore=12, spaceAfter=5
    )
    s['body'] = ParagraphStyle(
        '_body', fontSize=8.5, leading=12, fontName='Helvetica',
        textColor=Colors.PRIMARY
    )

    # Cell styles — uniform size for perfect vertical alignment
    SZ = 7.5
    LD = 10
    s['c']  = ParagraphStyle('_c',  fontSize=SZ, leading=LD, fontName='Helvetica', textColor=Colors.PRIMARY)
    s['cb'] = ParagraphStyle('_cb', fontSize=SZ, leading=LD, fontName='Helvetica-Bold', textColor=Colors.PRIMARY)
    s['cr'] = ParagraphStyle('_cr', fontSize=SZ, leading=LD, fontName='Helvetica', textColor=Colors.PRIMARY, alignment=TA_RIGHT)
    s['cc'] = ParagraphStyle('_cc', fontSize=SZ, leading=LD, fontName='Helvetica', textColor=Colors.PRIMARY, alignment=TA_CENTER)
    s['cm'] = ParagraphStyle('_cm', fontSize=6.5, leading=9, fontName='Helvetica', textColor=Colors.MUTED)  # muted/small

    # Header cell (white on dark)
    s['th'] = ParagraphStyle('_th', fontSize=7, leading=9.5, fontName='Helvetica-Bold',
                             textColor=Colors.TH_TEXT)
    s['thr'] = ParagraphStyle('_thr', fontSize=7, leading=9.5, fontName='Helvetica-Bold',
                              textColor=Colors.TH_TEXT, alignment=TA_RIGHT)
    s['thc'] = ParagraphStyle('_thc', fontSize=7, leading=9.5, fontName='Helvetica-Bold',
                              textColor=Colors.TH_TEXT, alignment=TA_CENTER)

    # KPI
    s['kv'] = ParagraphStyle('_kv', fontSize=15, leading=20, fontName='Helvetica-Bold',
                             alignment=TA_CENTER, textColor=Colors.PRIMARY)
    s['kl'] = ParagraphStyle('_kl', fontSize=7, leading=9.5, fontName='Helvetica',
                             alignment=TA_CENTER, textColor=Colors.MUTED)

    s['footer'] = ParagraphStyle('_ft', fontSize=6.5, leading=8.5, fontName='Helvetica',
                                 textColor=Colors.BRAND_TEXT, alignment=TA_CENTER)

    _CACHE = s
    return s


# ─────────────────────────────────────────────
# CELL HELPERS
# ─────────────────────────────────────────────
def _P(text, style_key='c'):
    return Paragraph(str(text) if text else '', _S()[style_key])

def _Pnl(value):
    if value is None or value == '':
        return _P('', 'cr')
    v = float(value)
    c = "#117a3e" if v >= 0 else "#c0392b"
    pfx = "+" if v > 0 else ""
    return _P(f'<font color="{c}"><b>{pfx}{int(v):,}</b></font>', 'cr')

def _Num(value, dec=2):
    if value is None or value == '' or value == 0:
        return _P('', 'cr')
    return _P(f'{float(value):,.{dec}f}', 'cr')

def _TH(text, align='left'):
    """Table header cell — white bold on dark background."""
    key = 'th' if align == 'left' else ('thr' if align == 'right' else 'thc')
    return Paragraph(str(text), _S()[key])


# ─────────────────────────────────────────────
# TABLE BUILDER
# ─────────────────────────────────────────────
def _table(headers, rows, col_widths, header_aligns=None, row_highlights=None):
    """
    Build a table with all cells as Paragraphs.
    headers: list of (text, align) tuples or just strings
    """
    s = _S()

    # Build header row
    if header_aligns:
        h_row = [_TH(h, a) for h, a in zip(headers, header_aligns)]
    else:
        h_row = [_TH(h) for h in headers]

    all_rows = [h_row] + rows
    table = Table(all_rows, colWidths=col_widths, repeatRows=1,
                  hAlign='LEFT')  # LEFT-ALIGN the whole table

    cmds = [
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),

        # Header
        ('BACKGROUND',    (0, 0), (-1, 0), Colors.TH_BG),
        ('TOPPADDING',    (0, 0), (-1, 0), 5),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 5),

        # Borders
        ('LINEBELOW',     (0, 0), (-1, 0), 0.8, Colors.ACCENT),
        ('LINEBELOW',     (0, 1), (-1, -1), 0.3, Colors.BORDER),
        ('LINEAFTER',     (0, 0), (-2, -1), 0.2, Colors.BORDER),
    ]

    # Alternating rows + highlights
    for i in range(1, len(all_rows)):
        if row_highlights and i in row_highlights:
            cmds.append(('BACKGROUND', (0, i), (-1, i), row_highlights[i]))
        elif i % 2 == 0:
            cmds.append(('BACKGROUND', (0, i), (-1, i), Colors.ROW_ALT))

    table.setStyle(TableStyle(cmds))
    return table


# ─────────────────────────────────────────────
# REPORT CLASS
# ─────────────────────────────────────────────
class BacktestReport:
    """Reusable PDF report for any AlgoSystem backtest."""

    def __init__(self, strategy_name: str, date: str, output_path: str = None,
                 orientation: str = "landscape"):
        self.strategy_name = strategy_name
        self.date = date
        self.output_path = output_path or f"reports/{strategy_name.replace(' ', '_')}_{date}.pdf"
        self.orientation = orientation

        self.config: dict = {}
        self.entry_legs: list[dict] = []
        self.entry_spot: float = 0
        self.atm_strike: int = 0
        self.combined_premium: float = 0
        self.net_credit: float = 0
        self.events: list[dict] = []
        self.trade_legs: list[dict] = []
        self.total_pnl: float = 0
        self.adjustment_cycles: int = 0
        self.g1_events: bool = False
        self.total_legs_traded: int = 0
        self.custom_kpis: list[dict] = []
        self.custom_sections: list[dict] = []

    def set_config(self, config: dict):
        self.config = config

    def set_entry_summary(self, spot: float, atm: int, legs: list[dict],
                          combined_premium: float = 0, net_credit: float = 0):
        self.entry_spot = spot
        self.atm_strike = atm
        self.entry_legs = legs
        self.combined_premium = combined_premium
        self.net_credit = net_credit

    def add_events(self, events: list[dict]):
        self.events = events

    def set_trade_summary(self, legs: list[dict], total_pnl: float,
                          cycles: int = 0, g1_events: bool = False):
        self.trade_legs = legs
        self.total_pnl = total_pnl
        self.adjustment_cycles = cycles
        self.g1_events = g1_events
        self.total_legs_traded = len(legs)

    def add_kpi(self, label: str, value, color=None):
        self.custom_kpis.append({'label': label, 'value': value, 'color': color})

    def add_section(self, title: str, elements: list):
        self.custom_sections.append({'title': title, 'elements': elements})

    # ─────────────────────────────────────────
    # GENERATE
    # ─────────────────────────────────────────
    def generate(self) -> str:
        os.makedirs(os.path.dirname(self.output_path) if os.path.dirname(self.output_path) else '.', exist_ok=True)

        pagesize = landscape(A4) if self.orientation == "landscape" else A4
        doc = SimpleDocTemplate(
            self.output_path, pagesize=pagesize,
            leftMargin=16*mm, rightMargin=16*mm,
            topMargin=10*mm, bottomMargin=10*mm
        )

        st = _S()
        story = []
        W = pagesize[0] - 32*mm  # usable width

        # ════════════════════════════════════════
        # BRAND BAR
        # ════════════════════════════════════════
        brand_bar = Table(
            [[Paragraph(f"<b>{BRAND_NAME}</b>", st['brand']),
              Paragraph(f"Confidential", st['brand'])]],
            colWidths=[W * 0.7, W * 0.3],
            hAlign='LEFT'
        )
        brand_bar.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), Colors.BRAND_BAR),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING',   (0, 0), (0, 0), 10),
            ('RIGHTPADDING',  (-1, -1), (-1, -1), 10),
            ('ALIGN',         (-1, 0), (-1, 0), 'RIGHT'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(brand_bar)
        story.append(Spacer(1, 8))

        # ════════════════════════════════════════
        # TITLE BLOCK
        # ════════════════════════════════════════
        story.append(Paragraph("Backtest Report", st['title']))
        story.append(Paragraph(
            f"<b>Strategy:</b> {self.strategy_name} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Date:</b> {self.date} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M')}",
            st['subtitle']
        ))

        # Config line
        if self.config:
            cfg_parts = [f"<b>{k}:</b> {v}" for k, v in self.config.items()]
            story.append(Paragraph(" &nbsp;&nbsp;|&nbsp;&nbsp; ".join(cfg_parts), st['subtitle']))

        story.append(HRFlowable(width="100%", thickness=0.6, color=Colors.BORDER, spaceAfter=6))

        # ════════════════════════════════════════
        # KPI CARDS
        # ════════════════════════════════════════
        kpis = []

        pnl_c = Colors.PROFIT if self.total_pnl >= 0 else Colors.LOSS
        pfx = "+" if self.total_pnl > 0 else ""
        kpis.append(self._kpi(f"{pfx}{int(self.total_pnl):,}", "Net P&L", pnl_c,
                              Colors.TAG_GREEN if self.total_pnl >= 0 else Colors.TAG_RED))

        if self.combined_premium > 0:
            kpis.append(self._kpi(f"{self.combined_premium:.1f}", "Combined Premium"))

        if self.adjustment_cycles > 0:
            kpis.append(self._kpi(str(self.adjustment_cycles), "Adj Cycles"))

        kpis.append(self._kpi(str(self.total_legs_traded), "Legs Traded"))

        if self.g1_events:
            kpis.append(self._kpi("YES", "Both-SL Event", Colors.LOSS, Colors.TAG_RED))

        for k in self.custom_kpis:
            kpis.append(self._kpi(str(k['value']), k['label'], k.get('color')))

        if kpis:
            n = len(kpis)
            card_w = min(W / n, 180)

            # Separate content from bg colors
            kpi_cells = [[k[0], k[1]] for k in kpis]  # [value_para, label_para]
            kpi_bgs = [k[2] for k in kpis]             # bg colors

            kpi_table = Table([kpi_cells], colWidths=[card_w] * n, hAlign='LEFT')
            kpi_cmds = [
                ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
                ('TOPPADDING',    (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ]
            for i, bg in enumerate(kpi_bgs):
                kpi_cmds.append(('BACKGROUND', (i, 0), (i, 0), bg))
                if i > 0:
                    kpi_cmds.append(('LINEBEFORE', (i, 0), (i, 0), 0.5, colors.white))

            kpi_cmds.append(('BOX', (0, 0), (-1, -1), 0.5, Colors.BORDER))
            kpi_table.setStyle(TableStyle(kpi_cmds))
            story.append(kpi_table)
            story.append(Spacer(1, 8))

        # ════════════════════════════════════════
        # ENTRY SUMMARY
        # ════════════════════════════════════════
        if self.entry_legs:
            story.append(Paragraph("Entry Summary", st['section']))

            anchor = f"Spot: <b>{self.entry_spot:.2f}</b> &nbsp;&bull;&nbsp; ATM: <b>{self.atm_strike}</b>"
            if self.net_credit:
                anchor += f" &nbsp;&bull;&nbsp; Net Credit: <b>{int(self.net_credit):,}</b>"
            story.append(Paragraph(anchor, st['body']))
            story.append(Spacer(1, 3))

            cw = [W*r for r in [0.12, 0.24, 0.09, 0.12, 0.12, 0.07]]
            ha = ['left', 'left', 'right', 'right', 'right', 'center']
            rows = []
            hl = {}
            for i, leg in enumerate(self.entry_legs):
                sl = leg.get('sl', 0)
                rows.append([
                    _P(leg['key'], 'cb'),
                    _P(leg.get('symbol', ''), 'cm'),
                    _Num(leg.get('strike', ''), 0),
                    _Num(leg['entry']),
                    _P(f"{sl:.2f}" if sl < 9999 else "—", 'cr'),
                    _P(str(leg.get('qty', '')), 'cc'),
                ])
                idx = i + 1
                hl[idx] = Colors.TAG_RED if leg.get('qty', 0) < 0 else Colors.TAG_GREEN

            story.append(_table(
                ['Leg', 'Symbol', 'Strike', 'Entry', 'SL', 'Qty'],
                rows, cw, header_aligns=ha, row_highlights=hl
            ))
            story.append(Spacer(1, 6))

        # ════════════════════════════════════════
        # INTRADAY EVENTS
        # ════════════════════════════════════════
        if self.events:
            story.append(Paragraph("Intraday Events", st['section']))

            cw = [W*r for r in [0.05, 0.065, 0.26, 0.20, 0.075, 0.09, 0.145]]
            ha = ['center', 'right', 'left', 'left', 'right', 'right', 'left']
            rows = []
            hl = {}
            for i, e in enumerate(self.events):
                sp = e.get('spot', 0)
                pr = e.get('price', 0)
                pnl = e.get('pnl')

                rows.append([
                    _P(e.get('time', ''), 'cc'),
                    _Num(sp, 1) if sp else _P('', 'cr'),
                    _P(e.get('action', '')),
                    _P(e.get('symbol', ''), 'cm'),
                    _Num(pr) if pr else _P('', 'cr'),
                    _Pnl(pnl) if pnl is not None else _P('', 'cr'),
                    _P(e.get('state', ''), 'cm'),
                ])

                idx = i + 1
                a = e.get('action', '')
                if 'SL HIT' in a or 'BOTH SIDES' in a:
                    hl[idx] = Colors.TAG_RED
                elif 'RE-ENTRY' in a or 'RE-OPEN' in a or 'REVERSION' in a:
                    hl[idx] = Colors.TAG_BLUE
                elif 'ADJUSTED' in a or 'FLIPPED' in a:
                    hl[idx] = Colors.TAG_AMBER
                elif ('ENTRY' in a.upper() and 'RE' not in a.upper()) or 'COMPLETE' in a or 'NEUTRAL' in e.get('state', ''):
                    if 'CLOSE' not in a and 'OPEN' not in a:
                        hl[idx] = Colors.TAG_GREEN
                elif 'FINAL' in a:
                    hl[idx] = Colors.TAG_GREY

            story.append(_table(
                ['Time', 'Spot', 'Action', 'Symbol', 'Price', 'P&L', 'State'],
                rows, cw, header_aligns=ha, row_highlights=hl
            ))
            story.append(Spacer(1, 6))

        # ════════════════════════════════════════
        # TRADE SUMMARY
        # ════════════════════════════════════════
        if self.trade_legs:
            story.append(Paragraph("Trade Summary", st['section']))

            cw = [W*r for r in [0.18, 0.11, 0.11, 0.07, 0.14, 0.09, 0.09]]
            ha = ['left', 'right', 'right', 'center', 'right', 'center', 'center']
            rows = []
            hl = {}
            for i, leg in enumerate(self.trade_legs):
                pv = leg.get('pnl', 0)
                rows.append([
                    _P(leg['key'], 'cb'),
                    _Num(leg['entry']),
                    _Num(leg['exit']),
                    _P(str(leg.get('qty', '')), 'cc'),
                    _Pnl(pv),
                    _P(leg.get('entry_time', ''), 'cc'),
                    _P(leg.get('exit_time', ''), 'cc'),
                ])
                idx = i + 1
                hl[idx] = Colors.TAG_GREEN if pv >= 0 else Colors.TAG_RED

            story.append(_table(
                ['Leg', 'Entry', 'Exit', 'Qty', 'P&L', 'In', 'Out'],
                rows, cw, header_aligns=ha, row_highlights=hl
            ))
            story.append(Spacer(1, 5))

            # ── P&L Banner ──
            pnl_hex = "#117a3e" if self.total_pnl >= 0 else "#c0392b"
            pnl_bg = Colors.TAG_GREEN if self.total_pnl >= 0 else Colors.TAG_RED
            pfx = "+" if self.total_pnl > 0 else ""

            banner_style = ParagraphStyle('_bn', fontSize=11, leading=15,
                                          fontName='Helvetica-Bold', alignment=TA_LEFT)
            banner_text = f'<font color="{pnl_hex}">Net Realized P&amp;L: {pfx}{int(self.total_pnl):,}</font>'

            stats_text = f"Adjustment Cycles: {self.adjustment_cycles} &nbsp;&bull;&nbsp; Legs Traded: {self.total_legs_traded}"
            if self.g1_events:
                stats_text += ' &nbsp;&bull;&nbsp; <font color="#c0392b"><b>Both-SL: YES</b></font>'

            banner = Table([
                [Paragraph(banner_text, banner_style),
                 Paragraph(stats_text, st['body'])]
            ], colWidths=[W * 0.35, W * 0.45], hAlign='LEFT')
            banner.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (0, 0), pnl_bg),
                ('BACKGROUND',    (1, 0), (1, 0), Colors.ACCENT_LIGHT),
                ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING',    (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                ('LEFTPADDING',   (0, 0), (-1, -1), 10),
                ('BOX',           (0, 0), (-1, -1), 0.5, Colors.BORDER),
                ('LINEBEFORE',    (1, 0), (1, 0), 0.5, Colors.BORDER),
            ]))
            story.append(banner)

        # ════════════════════════════════════════
        # CUSTOM SECTIONS
        # ════════════════════════════════════════
        for sec in self.custom_sections:
            story.append(Paragraph(sec['title'], st['section']))
            for elem in sec['elements']:
                story.append(elem)

        # ════════════════════════════════════════
        # FOOTER BAR
        # ════════════════════════════════════════
        story.append(Spacer(1, 14))
        footer_bar = Table(
            [[Paragraph(
                f"{BRAND_SHORT} &bull; {self.strategy_name} &bull; {self.date} &bull; "
                f"Generated {datetime.now().strftime('%d %b %Y %H:%M:%S')}",
                st['footer']
            )]],
            colWidths=[W], hAlign='LEFT'
        )
        footer_bar.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), Colors.BRAND_BAR),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING',   (0, 0), (-1, -1), 10),
        ]))
        story.append(footer_bar)

        doc.build(story)
        print(f"  Report saved: {self.output_path}")
        return self.output_path

    # ─────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────
    def _kpi(self, value_text, label, val_color=None, bg_color=None):
        """Build a KPI card as [value_para, label_para, bg_color]."""
        st = _S()
        vc = val_color or Colors.PRIMARY
        bg = bg_color or Colors.ACCENT_LIGHT
        vs = ParagraphStyle('_kvx', parent=st['kv'], textColor=vc)
        return [Paragraph(str(value_text), vs), Paragraph(label, st['kl']), bg]
