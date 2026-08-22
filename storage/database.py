from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

LATEST_SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS control_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  environment TEXT NOT NULL,
  live_setup_state TEXT NOT NULL,
  execution_state TEXT NOT NULL,
  agent_runtime_state TEXT NOT NULL,
  trading_mode TEXT NOT NULL,
  connection_state TEXT NOT NULL,
  kill_switch_active INTEGER NOT NULL DEFAULT 0,
  auto_demo_enabled INTEGER NOT NULL DEFAULT 0,
  scan_interval_seconds REAL NOT NULL DEFAULT 15,
  updated_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS control_audit_log (
  id INTEGER PRIMARY KEY,
  timestamp_ms INTEGER NOT NULL,
  actor TEXT NOT NULL,
  requested_action TEXT NOT NULL,
  previous_state_json TEXT NOT NULL,
  result_state_json TEXT NOT NULL,
  reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_control_audit_timestamp ON control_audit_log(timestamp_ms);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, timestamp_ms INTEGER NOT NULL, symbol TEXT NOT NULL,
  score INTEGER NOT NULL, confidence REAL NOT NULL, decision TEXT NOT NULL,
  reasons_json TEXT NOT NULL, signal_strength REAL
);
CREATE TABLE IF NOT EXISTS rejected_signals (
  id INTEGER PRIMARY KEY, timestamp_ms INTEGER NOT NULL, symbol TEXT NOT NULL,
  reason TEXT NOT NULL, signal_score INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS order_submissions (
  plan_id TEXT PRIMARY KEY, timestamp_ms INTEGER NOT NULL, symbol TEXT NOT NULL,
  side TEXT NOT NULL, strategy TEXT NOT NULL, response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_plans (
  plan_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, symbol TEXT NOT NULL,
  status TEXT NOT NULL, created_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL, rejection_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_plans_status ON trade_plans(status);
CREATE TABLE IF NOT EXISTS order_lifecycle (
  plan_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE, okx_order_id TEXT,
  symbol TEXT NOT NULL, side TEXT NOT NULL, requested_size REAL NOT NULL,
  filled_size REAL NOT NULL DEFAULT 0, average_fill_price REAL,
  expected_execution_price REAL, slippage_abs REAL, slippage_pct REAL,
  fee REAL, fee_currency TEXT, state TEXT NOT NULL,
  protection_state TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
  created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
  approved_at_ms INTEGER, submitted_at_ms INTEGER, filled_at_ms INTEGER,
  closed_at_ms INTEGER, backend TEXT NOT NULL, environment TEXT NOT NULL,
  raw_response_json TEXT, last_error TEXT,
  FOREIGN KEY(plan_id) REFERENCES trade_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_order_lifecycle_state ON order_lifecycle(state);
CREATE TABLE IF NOT EXISTS managed_positions (
  plan_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT NOT NULL, quantity REAL NOT NULL,
  entry_price REAL, state TEXT NOT NULL, protection_state TEXT NOT NULL,
  opened_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, closed_at_ms INTEGER,
  exit_order_id TEXT, protective_order_ids_json TEXT,
  FOREIGN KEY(plan_id) REFERENCES trade_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_managed_positions_state ON managed_positions(state);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY, timestamp_ms INTEGER NOT NULL, environment TEXT NOT NULL,
  backend TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, entry REAL NOT NULL,
  exit REAL, size REAL NOT NULL, stop REAL NOT NULL, take_profit REAL NOT NULL,
  fees REAL, slippage REAL, pnl REAL, holding_time_seconds REAL,
  strategy TEXT NOT NULL, signal_score INTEGER NOT NULL,
  plan_id TEXT, order_id TEXT, entry_time_ms INTEGER, exit_time_ms INTEGER,
  entry_price REAL, exit_price REAL, quantity REAL, gross_pnl REAL,
  slippage_abs REAL, slippage_pct REAL, net_pnl REAL, fee_status TEXT DEFAULT 'UNKNOWN'
  , exit_order_id TEXT
);
"""


def _ensure_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    present = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in present:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migrate(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "signals", "signal_strength REAL")
    for definition in (
        "plan_id TEXT", "order_id TEXT", "entry_time_ms INTEGER", "exit_time_ms INTEGER",
        "entry_price REAL", "exit_price REAL", "quantity REAL", "gross_pnl REAL",
        "slippage_abs REAL", "slippage_pct REAL", "net_pnl REAL",
        "fee_status TEXT DEFAULT 'UNKNOWN'",
        "exit_order_id TEXT",
    ):
        _ensure_column(connection, "trades", definition)
    for definition in ("exit_order_id TEXT", "protective_order_ids_json TEXT"):
        _ensure_column(connection, "managed_positions", definition)
    connection.execute("UPDATE signals SET signal_strength = confidence WHERE signal_strength IS NULL")
    connection.execute(
        "UPDATE trades SET fee_status = CASE WHEN fees IS NULL THEN 'UNKNOWN' ELSE 'LEGACY_ESTIMATE' END "
        "WHERE fee_status IS NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_plan_id ON trades(plan_id) WHERE plan_id IS NOT NULL"
    )
    applied_at_ms = int(datetime.now(UTC).timestamp() * 1000)
    for version in range(1, LATEST_SCHEMA_VERSION + 1):
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)",
            (version, applied_at_ms),
        )


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(version), 0) version FROM schema_migrations").fetchone()
    return int(row["version"])


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(SCHEMA)
    _migrate(connection)
    connection.commit()
    return connection
