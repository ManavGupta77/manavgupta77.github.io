# ==========================================
# CORE/DATABASE.PY
# ==========================================
import sqlite3
import os
import json
from datetime import datetime
from config_loader.settings import cfg
from utilities.logger import get_logger

logger = get_logger("database")

# ==========================================
# SCHEMA DEFINITIONS
# ==========================================

SCHEMA_TABLES = """
-- (Existing tables remain the same...)
-- [STRATEGIES, SESSIONS, POSITIONS, LEGS, ORDERS, STRATEGY_STATE]
-- (I will explicitly add the market_data table below)

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id     TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'DEFAULT',
    broker_id       TEXT NOT NULL DEFAULT 'UPSTOX',
    name            TEXT NOT NULL,
    description     TEXT,
    version         TEXT NOT NULL,
    strategy_type   TEXT NOT NULL,
    direction       TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    expiry_type     TEXT NOT NULL,
    structure       TEXT NOT NULL,
    entry_triggers  TEXT NOT NULL,
    default_params  TEXT,
    is_active       INTEGER NOT NULL DEFAULT 0,
    mode            TEXT NOT NULL DEFAULT 'PAPER',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'SCHEDULED',
    entry_time      TEXT,
    exit_time       TEXT,
    realized_pnl    REAL,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE TABLE IF NOT EXISTS positions (
    position_id     TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL,
    session_id      INTEGER NOT NULL,
    structure       TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    atm_strike      INTEGER,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    entry_time      TEXT NOT NULL,
    exit_time       TEXT,
    entry_premium   REAL,
    exit_premium    REAL,
    realized_pnl    REAL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS legs (
    leg_id          TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    session_id      INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    token           TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    option_type     TEXT NOT NULL,
    strike          INTEGER NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    lot_size        INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    entry_time      TEXT NOT NULL,
    exit_price      REAL,
    exit_time       TEXT,
    realized_pnl    REAL,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    role            TEXT NOT NULL DEFAULT 'MAIN',
    parent_leg_id   TEXT,
    FOREIGN KEY (position_id) REFERENCES positions(position_id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (parent_leg_id) REFERENCES legs(leg_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    leg_id          TEXT,
    position_id     TEXT,
    strategy_id     TEXT NOT NULL,
    session_id      INTEGER NOT NULL,
    order_type      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    token           TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    requested_price REAL,
    fill_price      REAL,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    broker_order_id TEXT,
    mode            TEXT NOT NULL DEFAULT 'PAPER',
    created_at      TEXT NOT NULL,
    error_message   TEXT,
    FOREIGN KEY (leg_id) REFERENCES legs(leg_id),
    FOREIGN KEY (position_id) REFERENCES positions(position_id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS strategy_state (
    strategy_id     TEXT PRIMARY KEY,
    session_id      INTEGER NOT NULL,
    state_json      TEXT NOT NULL DEFAULT '{}',
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

-- ==========================================
-- MARKET DATA: For Backtesting & Spot Proxy
-- ==========================================
CREATE TABLE IF NOT EXISTS market_data (
    timestamp       DATETIME NOT NULL,
    symbol          TEXT NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER,
    oi              INTEGER DEFAULT 0,
    PRIMARY KEY (timestamp, symbol)
);

-- ==========================================
-- OPTIONS DATA: For Backtesting (Breeze API)
-- ==========================================
CREATE TABLE IF NOT EXISTS options_ohlc (
    timestamp       TEXT NOT NULL,
    instrument_key  TEXT NOT NULL,
    tradingsymbol   TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER DEFAULT 0,
    oi              INTEGER DEFAULT 0,
    PRIMARY KEY (timestamp, instrument_key)
);


"""

SCHEMA_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_date_strategy ON sessions(date, strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_session ON positions(session_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_legs_position ON legs(position_id);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id);
CREATE INDEX IF NOT EXISTS idx_market_symbol_ts ON market_data (symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_opts_instrument_ts ON options_ohlc (instrument, timestamp);
CREATE INDEX IF NOT EXISTS idx_opts_expiry_strike ON options_ohlc (expiry, strike, option_type);
CREATE INDEX IF NOT EXISTS idx_opts_symbol ON options_ohlc (tradingsymbol);

"""

# ==========================================
# DATABASE CLASS
# ==========================================

class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or str(cfg.DB_FULL_PATH)
        self.connection = None

    def connect(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        logger.info("Database connected", path=self.db_path)
        return self

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def _get_conn(self):
        if not self.connection:
            self.connect()
        return self.connection

    # --- SCHEMA ---
    def create_tables(self):
        conn = self._get_conn()
        for statement in SCHEMA_TABLES.split(';'):
            if statement.strip(): conn.execute(statement)
        for statement in SCHEMA_INDEXES.split(';'):
            if statement.strip(): conn.execute(statement)
        conn.commit()
        logger.info("Schema initialized")

    def get_table_list(self):
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in rows]
        
    def get_table_info(self, table_name):
        rows = self.query(f"PRAGMA table_info({table_name})")
        return [{"name": r["name"], "type": r["type"]} for r in rows]

    # --- GENERIC ---
    def execute(self, sql, params=None):
        conn = self._get_conn()
        cursor = conn.execute(sql, params or [])
        conn.commit()
        return cursor.lastrowid

    def query(self, sql, params=None):
        conn = self._get_conn()
        cursor = conn.execute(sql, params or [])
        return [dict(row) for row in cursor.fetchall()]

    def query_one(self, sql, params=None):
        conn = self._get_conn()
        cursor = conn.execute(sql, params or [])
        row = cursor.fetchone()
        return dict(row) if row else None

    # --- HELPERS ---
    def generate_id(self, prefix, table, id_column):
        row = self.query_one(f"SELECT {id_column} FROM {table} ORDER BY rowid DESC LIMIT 1")
        if row:
            try:
                num = int(row[id_column].split('_')[1]) + 1
            except IndexError:
                num = 1
        else:
            num = 1
        return f"{prefix}_{num:04d}"

    @staticmethod
    def now():
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def today():
        return datetime.now().strftime("%Y-%m-%d")

    # --- STRATEGIES ---
    def insert_strategy(self, config):
        now = self.now()
        self.execute("""
            INSERT OR REPLACE INTO strategies
            (strategy_id, user_id, broker_id, name, description, version,
            strategy_type, direction, instrument, expiry_type, structure,
            entry_triggers, default_params, is_active, mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            config["strategy_id"], config.get("user_id", "DEFAULT"),
            config.get("broker_id", "UPSTOX"), config["name"],
            config.get("description", ""), config["version"],
            config["strategy_type"], config["direction"],
            config["instrument"], config["expiry_type"],
            config["structure"], json.dumps(config.get("entry_triggers", [])),
            json.dumps(config.get("params", {})), config.get("is_active", 0),
            config.get("mode", "PAPER"), now, now
        ])
        return config["strategy_id"]

    def get_strategy(self, strategy_id):
        return self.query_one("SELECT * FROM strategies WHERE strategy_id = ?", [strategy_id])
    
    def get_active_strategies(self):
        return self.query("SELECT * FROM strategies WHERE is_active = 1")

    # --- SESSIONS ---
    def create_session(self, strategy_id, date=None):
        date = date or self.today()
        existing = self.get_session_by_date(strategy_id, date)
        if existing: return existing["session_id"]
        
        return self.execute("INSERT INTO sessions (date, strategy_id, status, created_at) VALUES (?, ?, 'SCHEDULED', ?)", 
                            [date, strategy_id, self.now()])

    def get_session(self, session_id):
        return self.query_one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    
    def get_session_by_date(self, strategy_id, date):
        return self.query_one("SELECT * FROM sessions WHERE strategy_id = ? AND date = ?", [strategy_id, date])

    def update_session(self, session_id, **kwargs):
        if not kwargs: return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        self.execute(f"UPDATE sessions SET {set_clause} WHERE session_id = ?", list(kwargs.values()) + [session_id])

    # --- POSITIONS ---
    def insert_position(self, p):
        self.execute("""
            INSERT INTO positions (position_id, strategy_id, session_id, structure, instrument, expiry, atm_strike, status, entry_time, entry_premium)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, [p["position_id"], p["strategy_id"], p["session_id"], p["structure"], p["instrument"], 
              p["expiry"], p.get("atm_strike"), p["entry_time"], p.get("entry_premium")])
    
    def get_open_positions(self, strategy_id=None):
        if strategy_id:
            return self.query("SELECT * FROM positions WHERE status = 'OPEN' AND strategy_id = ?", [strategy_id])
        return self.query("SELECT * FROM positions WHERE status = 'OPEN'")
    
    def close_position(self, pid, exit_time, exit_prem, pnl):
        self.execute("UPDATE positions SET status='CLOSED', exit_time=?, exit_premium=?, realized_pnl=? WHERE position_id=?",
                     [exit_time, exit_prem, pnl, pid])

    # --- LEGS ---
    def insert_leg(self, l):
        self.execute("""
            INSERT INTO legs (leg_id, position_id, strategy_id, session_id, symbol, token, exchange, option_type, 
            strike, side, quantity, lot_size, entry_price, entry_time, status, role, parent_leg_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, [l["leg_id"], l["position_id"], l["strategy_id"], l["session_id"], l["symbol"], l["token"],
              l["exchange"], l["option_type"], l["strike"], l["side"], l["quantity"], l["lot_size"],
              l["entry_price"], l["entry_time"], l.get("role", "MAIN"), l.get("parent_leg_id")])

    def get_legs_for_position(self, pid):
        return self.query("SELECT * FROM legs WHERE position_id = ? ORDER BY entry_time", [pid])
        
    def close_leg(self, lid, exit_price, exit_time, pnl):
        self.execute("UPDATE legs SET status='CLOSED', exit_price=?, exit_time=?, realized_pnl=? WHERE leg_id=?", 
                     [exit_price, exit_time, pnl, lid])

    # --- ORDERS ---
    def insert_order(self, o):
        self.execute("""
            INSERT INTO orders (order_id, leg_id, position_id, strategy_id, session_id, order_type, symbol, token, 
            exchange, side, quantity, requested_price, fill_price, status, broker_order_id, mode, created_at, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [o["order_id"], o.get("leg_id"), o.get("position_id"), o["strategy_id"], o["session_id"],
              o["order_type"], o["symbol"], o["token"], o["exchange"], o["side"], o["quantity"],
              o.get("requested_price"), o.get("fill_price"), o.get("status", "PENDING"), o.get("broker_order_id"),
              o.get("mode", cfg.TRADING_MODE), o.get("created_at", self.now()), o.get("error_message")])

    def get_orders_for_session(self, sid):
        return self.query("SELECT * FROM orders WHERE session_id = ? ORDER BY created_at", [sid])

    def update_order(self, order_id, **kwargs):
        if not kwargs: return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        self.execute(f"UPDATE orders SET {set_clause} WHERE order_id = ?", list(kwargs.values()) + [order_id])

    # --- MARKET DATA (NEW) ---
    def get_market_data(self, symbol, start_time, end_time=None):
        """Fetch market data for backtesting."""
        if end_time:
            return self.query("SELECT * FROM market_data WHERE symbol = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp", 
                              [symbol, start_time, end_time])
        return self.query("SELECT * FROM market_data WHERE symbol = ? AND timestamp >= ? ORDER BY timestamp", 
                          [symbol, start_time])

    # --- REPORTING ---
    def get_session_summary(self, session_id):
        s = self.get_session(session_id)
        if not s: return None
        s["total_orders"] = self.query_one("SELECT COUNT(*) as c FROM orders WHERE session_id = ?", [session_id])["c"]
        s["total_positions"] = self.query_one("SELECT COUNT(*) as c FROM positions WHERE session_id = ?", [session_id])["c"]
        return s

# Singleton
db = Database()

# ==========================================
# STANDALONE TEST
# ==========================================
if __name__ == "__main__":
    print("🚀 Starting Database Test...")
    db.connect()
    db.create_tables()
    
    # Test Strategy
    strat_id = "STRAT_TEST"
    db.insert_strategy({
        "strategy_id": strat_id, "name": "Test Strat", "version": "1.0",
        "strategy_type": "INTRADAY", "direction": "NEUTRAL", "instrument": "NIFTY",
        "expiry_type": "WEEKLY", "structure": "STRADDLE", "params": {"qty": 1}
    })
    
    # Test Session
    sess_id = db.create_session(strat_id)
    print(f"✅ Session Created: {sess_id}")
    
    # Test IDs
    pos_id = db.generate_id("POS", "positions", "position_id")
    leg_id = db.generate_id("LEG", "legs", "leg_id")
    
    # Test Position (CORRECTED)
    now = db.now()
    db.insert_position({
        "position_id": pos_id, "strategy_id": strat_id, "session_id": sess_id,
        "structure": "STRADDLE", "instrument": "NIFTY", "expiry": "TEST",
        "entry_time": now, "entry_premium": 100.0, "atm_strike": 25000
    })
    
    # Test Leg (CORRECTED - Added position_id)
    db.insert_leg({
        "leg_id": leg_id, "position_id": pos_id, "strategy_id": strat_id,
        "session_id": sess_id, "symbol": "NIFTY25000CE", "token": "123",
        "exchange": "NFO", "option_type": "CE", "strike": 25000, "side": "SELL",
        "quantity": 50, "lot_size": 50, "entry_price": 50.0, "entry_time": now
    })
    
    print("✅ Insertions Successful.")
    
    # Test Read
    legs = db.get_legs_for_position(pos_id)
    print(f"📋 Retrieved {len(legs)} legs.")
    
    db.close()
    print("🎉 Test Complete.")