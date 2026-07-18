from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from bot.strategy import Position


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    rsi REAL NOT NULL,
                    macd REAL NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Migración para cambiar UNIQUE(account_name) por UNIQUE(account_name, symbol)
            # Primero verificamos si existe la tabla positions
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='positions'")
            if cursor.fetchone():
                cursor.execute("PRAGMA index_list(positions)")
                indexes = cursor.fetchall()
                migrate_needed = False
                for idx_info in indexes:
                    idx_name = idx_info["name"]
                    cursor.execute(f"PRAGMA index_info({idx_name})")
                    cols = [row["name"] for row in cursor.fetchall()]
                    if len(cols) == 1 and cols[0] == "account_name":
                        migrate_needed = True
                        break
                if migrate_needed:
                    cursor.execute("ALTER TABLE positions RENAME TO positions_old")
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS positions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            account_name TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            quantity REAL NOT NULL,
                            initial_quantity REAL NOT NULL DEFAULT 0,
                            entry_price REAL NOT NULL,
                            cost_usdt REAL NOT NULL DEFAULT 0,
                            stop_loss_price REAL NOT NULL,
                            take_profit_price REAL NOT NULL,
                            mode TEXT NOT NULL,
                            side TEXT NOT NULL DEFAULT 'LONG',
                            leverage REAL NOT NULL DEFAULT 1,
                            margin_used_usdt REAL NOT NULL DEFAULT 0,
                            liquidation_price REAL NOT NULL DEFAULT 0,
                            highest_price REAL NOT NULL DEFAULT 0,
                            trailing_stop_price REAL NOT NULL DEFAULT 0,
                            breakeven_armed INTEGER NOT NULL DEFAULT 0,
                            ladder_step INTEGER NOT NULL DEFAULT 0,
                            realized_pnl REAL NOT NULL DEFAULT 0,
                            status TEXT NOT NULL,
                            entry_order_id TEXT,
                            macro_aligned INTEGER NOT NULL DEFAULT 0,
                            opened_at TEXT,
                            last_funding_at TEXT,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(account_name, symbol)
                        )
                        """
                    )
                    cursor.execute("PRAGMA table_info(positions_old)")
                    old_cols = {col["name"] for col in cursor.fetchall()}
                    cursor.execute("PRAGMA table_info(positions)")
                    new_cols = [col["name"] for col in cursor.fetchall() if col["name"] != "id"]
                    common_cols = [c for c in new_cols if c in old_cols]
                    common_cols_str = ", ".join(common_cols)
                    cursor.execute(f"INSERT INTO positions ({common_cols_str}) SELECT {common_cols_str} FROM positions_old")
                    cursor.execute("DROP TABLE positions_old")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    initial_quantity REAL NOT NULL DEFAULT 0,
                    entry_price REAL NOT NULL,
                    cost_usdt REAL NOT NULL DEFAULT 0,
                    stop_loss_price REAL NOT NULL,
                    take_profit_price REAL NOT NULL,
                    mode TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'LONG',
                    leverage REAL NOT NULL DEFAULT 1,
                    margin_used_usdt REAL NOT NULL DEFAULT 0,
                    liquidation_price REAL NOT NULL DEFAULT 0,
                    highest_price REAL NOT NULL DEFAULT 0,
                    trailing_stop_price REAL NOT NULL DEFAULT 0,
                    breakeven_armed INTEGER NOT NULL DEFAULT 0,
                    ladder_step INTEGER NOT NULL DEFAULT 0,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    entry_order_id TEXT,
                    macro_aligned INTEGER NOT NULL DEFAULT 0,
                    opened_at TEXT,
                    last_funding_at TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(account_name, symbol)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    reason TEXT,
                    mode TEXT NOT NULL,
                    order_id TEXT,
                    context_json TEXT,
                    pnl REAL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    published_at TEXT,
                    sentiment_score REAL NOT NULL,
                    summary TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    paper_balance_usdt REAL NOT NULL,
                    paper_xrp_balance REAL NOT NULL,
                    estimated_equity REAL NOT NULL,
                    open_position INTEGER NOT NULL,
                    note TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS xrp_market_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, interval, timestamp)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    decision_score REAL NOT NULL,
                    confidence TEXT NOT NULL,
                    leverage REAL NOT NULL,
                    spread_pct REAL NOT NULL,
                    slippage_pct REAL NOT NULL,
                    close_price REAL NOT NULL,
                    volume_ratio REAL NOT NULL,
                    atr_pct REAL NOT NULL,
                    rsi REAL NOT NULL,
                    macd REAL NOT NULL,
                    historical_return REAL NOT NULL,
                    historical_positive_ratio REAL NOT NULL,
                    reason TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS circuit_breaker_state (
                    account_name TEXT PRIMARY KEY,
                    triggered INTEGER NOT NULL DEFAULT 0,
                    triggered_date TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._ensure_column(connection, "positions", "initial_quantity", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "cost_usdt", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "side", "TEXT NOT NULL DEFAULT 'LONG'")
            self._ensure_column(connection, "positions", "leverage", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(connection, "positions", "margin_used_usdt", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "liquidation_price", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "highest_price", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "trailing_stop_price", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "breakeven_armed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "ladder_step", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "realized_pnl", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "positions", "opened_at", "TEXT")
            self._ensure_column(connection, "positions", "last_funding_at", "TEXT")
            self._ensure_column(connection, "trades", "context_json", "TEXT")
            self._ensure_column(connection, "positions", "macro_aligned", "INTEGER NOT NULL DEFAULT 0")
            connection.commit()

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in existing:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )

    def save_price(self, account_name: str, symbol: str, row: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO prices (account_name, symbol, timestamp, close, volume, rsi, macd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_name,
                    symbol,
                    row["timestamp"].isoformat(),
                    float(row["close"]),
                    float(row["volume"]),
                    float(row["rsi_14"]),
                    float(row["macd"]),
                ),
            )
            connection.commit()

    def upsert_position(self, position: Position) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO positions (
                    account_name, symbol, quantity, initial_quantity, entry_price, cost_usdt, stop_loss_price,
                    take_profit_price, mode, side, leverage, margin_used_usdt, liquidation_price,
                    highest_price, trailing_stop_price, breakeven_armed, ladder_step,
                    realized_pnl, opened_at, last_funding_at, status, entry_order_id, macro_aligned, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(account_name, symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    quantity = excluded.quantity,
                    initial_quantity = excluded.initial_quantity,
                    entry_price = excluded.entry_price,
                    cost_usdt = excluded.cost_usdt,
                    stop_loss_price = excluded.stop_loss_price,
                    take_profit_price = excluded.take_profit_price,
                    mode = excluded.mode,
                    side = excluded.side,
                    leverage = excluded.leverage,
                    margin_used_usdt = excluded.margin_used_usdt,
                    liquidation_price = excluded.liquidation_price,
                    highest_price = excluded.highest_price,
                    trailing_stop_price = excluded.trailing_stop_price,
                    breakeven_armed = excluded.breakeven_armed,
                    ladder_step = excluded.ladder_step,
                    realized_pnl = excluded.realized_pnl,
                    opened_at = excluded.opened_at,
                    last_funding_at = excluded.last_funding_at,
                    status = excluded.status,
                    entry_order_id = excluded.entry_order_id,
                    macro_aligned = excluded.macro_aligned,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    position.account_name,
                    position.symbol,
                    position.quantity,
                    position.initial_quantity,
                    position.entry_price,
                    position.cost_usdt,
                    position.stop_loss_price,
                    position.take_profit_price,
                    position.mode,
                    position.side,
                    position.leverage,
                    position.margin_used_usdt,
                    position.liquidation_price,
                    position.highest_price,
                    position.trailing_stop_price,
                    int(position.breakeven_armed),
                    position.ladder_step,
                    position.realized_pnl,
                    position.opened_at,
                    position.last_funding_at,
                    position.status,
                    position.entry_order_id,
                    int(position.macro_aligned),
                ),
            )
            connection.commit()

    def get_open_position(self, account_name: str, symbol: str) -> Position | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                    SELECT account_name, symbol, quantity, initial_quantity, entry_price, cost_usdt, stop_loss_price,
                       take_profit_price, mode, side, leverage, margin_used_usdt, liquidation_price,
                       highest_price, trailing_stop_price, breakeven_armed, ladder_step,
                       realized_pnl, opened_at, last_funding_at, status, entry_order_id, macro_aligned
                FROM positions
                WHERE account_name = ? AND symbol = ? AND status = 'OPEN'
                """,
                (account_name, symbol),
            ).fetchone()

        if not row:
            return None

        return Position(
            account_name=row["account_name"],
            symbol=row["symbol"],
            quantity=row["quantity"],
            initial_quantity=row["initial_quantity"],
            entry_price=row["entry_price"],
            cost_usdt=row["cost_usdt"],
            stop_loss_price=row["stop_loss_price"],
            take_profit_price=row["take_profit_price"],
            mode=row["mode"],
            side=row["side"],
            leverage=row["leverage"],
            margin_used_usdt=row["margin_used_usdt"],
            liquidation_price=row["liquidation_price"],
            highest_price=row["highest_price"],
            trailing_stop_price=row["trailing_stop_price"],
            breakeven_armed=bool(row["breakeven_armed"]),
            ladder_step=row["ladder_step"],
            realized_pnl=row["realized_pnl"],
            opened_at=row["opened_at"],
            last_funding_at=row["last_funding_at"],
            status=row["status"],
            entry_order_id=row["entry_order_id"],
            macro_aligned=bool(row["macro_aligned"]),
        )

    def close_position(self, account_name: str, symbol: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE positions
                SET status = 'CLOSED', updated_at = CURRENT_TIMESTAMP
                WHERE account_name = ? AND symbol = ? AND status = 'OPEN'
                """,
                (account_name, symbol),
            )
            connection.commit()

    def save_audit_event(
        self,
        account_name: str,
        symbol: str,
        approved: bool,
        decision_score: float,
        confidence: str,
        leverage: float,
        spread_pct: float,
        slippage_pct: float,
        close_price: float,
        volume_ratio: float,
        atr_pct: float,
        rsi: float,
        macd: float,
        historical_return: float,
        historical_positive_ratio: float,
        reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    account_name, symbol, approved, decision_score, confidence, leverage,
                    spread_pct, slippage_pct, close_price, volume_ratio, atr_pct, rsi,
                    macd, historical_return, historical_positive_ratio, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_name,
                    symbol,
                    int(approved),
                    decision_score,
                    confidence,
                    leverage,
                    spread_pct,
                    slippage_pct,
                    close_price,
                    volume_ratio,
                    atr_pct,
                    rsi,
                    macd,
                    historical_return,
                    historical_positive_ratio,
                    reason,
                ),
            )
            connection.commit()

    def save_trade(
        self,
        account_name: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        reason: str,
        mode: str,
        order_id: str | None,
        context_json: str | None = None,
        pnl: float = 0.0,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trades (account_name, symbol, side, quantity, price, reason, mode, order_id, context_json, pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (account_name, symbol, side, quantity, price, reason, mode, order_id, context_json, pnl),
            )
            connection.commit()

    def save_news_item(
        self,
        symbol: str,
        provider: str,
        title: str,
        source: str,
        url: str,
        published_at: str,
        sentiment_score: float,
        summary: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO news_events (symbol, provider, title, source, url, published_at, sentiment_score, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, provider, title, source, url, published_at, sentiment_score, summary),
            )
            connection.commit()

    def save_account_snapshot(
        self,
        account_name: str,
        mode: str,
        symbol: str,
        close_price: float,
        paper_balance_usdt: float,
        paper_xrp_balance: float,
        estimated_equity: float,
        open_position: bool,
        note: str = "",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO account_snapshots (
                    account_name, mode, symbol, close_price, paper_balance_usdt,
                    paper_xrp_balance, estimated_equity, open_position, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_name,
                    mode,
                    symbol,
                    close_price,
                    paper_balance_usdt,
                    paper_xrp_balance,
                    estimated_equity,
                    int(open_position),
                    note,
                ),
            )
            connection.commit()

    def get_latest_account_snapshot(self, account_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT account_name, mode, symbol, close_price, paper_balance_usdt,
                       paper_xrp_balance, estimated_equity, open_position, note, created_at
                FROM account_snapshots
                WHERE account_name = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (account_name,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def save_history_rows(
        self,
        source: str,
        interval: str,
        rows: list[tuple[str, float, float, float, float, float]],
    ) -> int:
        if not rows:
            return 0
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO xrp_market_history (
                    source, interval, timestamp, open, high, low, close, volume
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (source, interval, timestamp, open_price, high, low, close, volume)
                    for timestamp, open_price, high, low, close, volume in rows
                ],
            )
            inserted = connection.total_changes
            connection.commit()
            return inserted

    def get_hourly_history_context(self, hour_utc: int) -> dict[str, float]:
        with self._connect() as connection:
            row = connection.execute(
                """
                WITH base AS (
                    SELECT
                        timestamp,
                        CAST(strftime('%H', timestamp) AS INTEGER) AS hour_utc,
                        open,
                        high,
                        low,
                        close,
                        volume,
                        CASE WHEN open != 0 THEN (close - open) / open ELSE 0 END AS ret_pct,
                        CASE WHEN open != 0 THEN (high - low) / open ELSE 0 END AS range_pct
                    FROM xrp_market_history
                    WHERE interval = '1h'
                )
                SELECT
                    COALESCE(AVG(ret_pct), 0) AS avg_hourly_return,
                    COALESCE(AVG(CASE WHEN ret_pct > 0 THEN 1.0 ELSE 0.0 END), 0) AS positive_hour_ratio,
                    COALESCE(AVG(range_pct), 0) AS avg_hourly_range_pct,
                    COALESCE(AVG(CASE
                        WHEN timestamp >= datetime('now', '-30 day')
                        THEN volume
                        ELSE NULL
                    END), 0) AS avg_hourly_volume_30d,
                    COUNT(*) AS samples
                FROM base
                WHERE hour_utc = ?
                """,
                (hour_utc,),
            ).fetchone()

        if not row:
            return {
                "avg_hourly_return": 0.0,
                "positive_hour_ratio": 0.0,
                "avg_hourly_range_pct": 0.0,
                "avg_hourly_volume_30d": 0.0,
                "projected_hourly_volume_ratio": 0.0,
                "samples": 0.0,
            }

        return {
            "avg_hourly_return": float(row["avg_hourly_return"]),
            "positive_hour_ratio": float(row["positive_hour_ratio"]),
            "avg_hourly_range_pct": float(row["avg_hourly_range_pct"]),
            "avg_hourly_volume_30d": float(row["avg_hourly_volume_30d"]),
            "projected_hourly_volume_ratio": 0.0,
            "samples": float(row["samples"]),
        }

    def get_circuit_breaker_state(self, account_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT triggered, triggered_date FROM circuit_breaker_state WHERE account_name = ?",
                (account_name,),
            ).fetchone()
        if row is None:
            return None
        return {"triggered": bool(row["triggered"]), "triggered_date": row["triggered_date"]}

    def set_circuit_breaker_state(self, account_name: str, triggered: bool, date_utc: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO circuit_breaker_state (account_name, triggered, triggered_date)
                VALUES (?, ?, ?)
                ON CONFLICT(account_name) DO UPDATE SET
                    triggered = excluded.triggered,
                    triggered_date = excluded.triggered_date
                """,
                (account_name, int(triggered), date_utc),
            )
            connection.commit()

    def get_trades_today(self, account_name: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT side, quantity, price, reason, pnl, created_at
                FROM trades
                WHERE account_name = ?
                  AND date(created_at) = date('now')
                ORDER BY id ASC
                """,
                (account_name,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_positions_count(self, account_name: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) as cnt FROM positions WHERE account_name = ? AND status = 'OPEN'",
                (account_name,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def cleanup_old_prices(self, days_to_keep: int = 7) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM prices WHERE created_at < datetime('now', ? || ' days')",
                (f"-{days_to_keep}",),
            )
            deleted = cursor.rowcount
            connection.commit()
        # VACUUM debe ir fuera de la transacción
        conn = self._connect()
        conn.isolation_level = None
        conn.execute("VACUUM")
        conn.close()
        return deleted
