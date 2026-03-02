#!/usr/bin/env python3
"""
Trade History Module - SQLite-based persistent storage
Provides efficient querying and analysis of trade history
"""
import sqlite3
import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from contextlib import contextmanager

from paths import DATABASE_FILE

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trade_history")


class TradeHistoryDB:
    """SQLite-based trade history storage"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Consolidated single-DB mode (aster.db)
            db_path = DATABASE_FILE
        
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_history_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    notional REAL NOT NULL,
                    order_id TEXT,
                    status TEXT NOT NULL,
                    side_type TEXT,
                    pnl REAL,
                    commission REAL,
                    open_time INTEGER,
                    close_time INTEGER,
                    cycle_id TEXT,
                    signal_strength REAL,
                    confidence REAL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_th_trades_symbol ON trade_history_trades(symbol)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_th_trades_created_at ON trade_history_trades(created_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_th_trades_status ON trade_history_trades(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_th_trades_cycle_id ON trade_history_trades(cycle_id)
            """)
            
            # Positions table for tracking open positions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_history_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL,
                    unrealized_pnl REAL,
                    notional REAL NOT NULL,
                    leverage INTEGER DEFAULT 1,
                    open_time INTEGER,
                    cycle_id TEXT,
                    metadata TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Scale-ins table for tracking partial fills
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_history_scale_ins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_trade_id INTEGER,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    notional REAL NOT NULL,
                    scale_in_order_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (parent_trade_id) REFERENCES trade_history_trades(id)
                )
            """)
            
            conn.commit()
            logger.info(f"Trade history database initialized at {self.db_path}")
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with context manager, WAL mode, and retry logic"""
        import time
        import random
        
        max_retries = 5
        base_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                conn.row_factory = sqlite3.Row
                
                # Enable WAL mode for better concurrency
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout
                
                try:
                    yield conn
                finally:
                    conn.close()
                return
                
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                if "locked" in error_msg and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
                    logger.warning(f"Database locked, retry {attempt + 1}/{max_retries} after {delay:.2f}s: {e}")
                    time.sleep(delay)
                elif "locked" in error_msg:
                    logger.error(f"Database still locked after {max_retries} retries")
                    raise
                else:
                    raise
    
    def add_trade(self, trade_data: Dict[str, Any]) -> int:
        """Add a new trade to history"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Serialize metadata if present
            metadata = None
            if trade_data.get("metadata"):
                metadata = json.dumps(trade_data["metadata"])
            
            cursor.execute("""
                INSERT INTO trade_history_trades (
                    symbol, side, quantity, price, notional, order_id, status,
                    side_type, pnl, commission, open_time, close_time, cycle_id,
                    signal_strength, confidence, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get("symbol"),
                trade_data.get("side"),
                trade_data.get("quantity"),
                trade_data.get("price"),
                trade_data.get("notional"),
                trade_data.get("order_id"),
                trade_data.get("status"),
                trade_data.get("side_type"),
                trade_data.get("pnl"),
                trade_data.get("commission"),
                trade_data.get("open_time"),
                trade_data.get("close_time"),
                trade_data.get("cycle_id"),
                trade_data.get("signal_strength"),
                trade_data.get("confidence"),
                metadata
            ))
            
            conn.commit()
            return cursor.lastrowid
    
    def update_trade_pnl(self, trade_id: int, pnl: float, close_time: int = None):
        """Update trade PnL when trade is closed"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trade_history_trades 
                SET pnl = ?, close_time = ?, status = 'CLOSED'
                WHERE id = ?
            """, (pnl, close_time, trade_id))
            conn.commit()
    
    def get_trades(
        self,
        symbol: str = None,
        status: str = None,
        start_time: int = None,
        end_time: int = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get trades with optional filters"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM trade_history_trades WHERE 1=1"
            params = []
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            if status:
                query += " AND status = ?"
                params.append(status)
            
            if start_time:
                query += " AND open_time >= ?"
                params.append(start_time)
            
            if end_time:
                query += " AND open_time <= ?"
                params.append(end_time)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
    
    def get_trade_stats(self, symbol: str = None, days: int = 30) -> Dict[str, Any]:
        """Get trade statistics"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Time filter
            import time
            start_time = int((datetime.now().timestamp() - days * 24 * 3600) * 1000)
            
            query = """
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN status = 'FILLED' THEN 1 ELSE 0 END) as filled_trades,
                    SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) as error_trades,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    SUM(notional) as total_volume,
                    COUNT(DISTINCT symbol) as unique_symbols
                FROM trade_history_trades
                WHERE created_at >= datetime(?, 'unixepoch')
            """
            params = [start_time / 1000]
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            cursor.execute(query, params)
            row = cursor.fetchone()
            
            return {
                "total_trades": row[0] or 0,
                "filled_trades": row[1] or 0,
                "error_trades": row[2] or 0,
                "total_pnl": row[3] or 0.0,
                "avg_pnl": row[4] or 0.0,
                "total_volume": row[5] or 0.0,
                "unique_symbols": row[6] or 0,
                "win_rate": (row[1] / row[0] * 100) if row[0] and row[1] else 0.0
            }
    
    def upsert_position(self, position_data: Dict[str, Any]) -> int:
        """Insert or update a position"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            metadata = None
            if position_data.get("metadata"):
                metadata = json.dumps(position_data["metadata"])
            
            cursor.execute("""
                INSERT INTO trade_history_positions (
                    symbol, side, quantity, entry_price, current_price,
                    unrealized_pnl, notional, leverage, open_time, cycle_id, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    entry_price = excluded.entry_price,
                    current_price = excluded.current_price,
                    unrealized_pnl = excluded.unrealized_pnl,
                    notional = excluded.notional,
                    leverage = excluded.leverage,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                position_data.get("symbol"),
                position_data.get("side"),
                position_data.get("quantity"),
                position_data.get("entry_price"),
                position_data.get("current_price"),
                position_data.get("unrealized_pnl"),
                position_data.get("notional"),
                position_data.get("leverage", 1),
                position_data.get("open_time"),
                position_data.get("cycle_id"),
                metadata
            ))
            
            conn.commit()
            return cursor.lastrowid
    
    def close_position(self, symbol: str, pnl: float = 0.0):
        """Close a position"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trade_history_positions WHERE symbol = ?", (symbol,))
            conn.commit()
    
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_history_positions")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def add_scale_in(self, scale_in_data: Dict[str, Any]) -> int:
        """Record a scale-in trade"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trade_history_scale_ins (
                    parent_trade_id, symbol, side, quantity, price,
                    notional, scale_in_order_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                scale_in_data.get("parent_trade_id"),
                scale_in_data.get("symbol"),
                scale_in_data.get("side"),
                scale_in_data.get("quantity"),
                scale_in_data.get("price"),
                scale_in_data.get("notional"),
                scale_in_data.get("scale_in_order_id")
            ))
            conn.commit()
            return cursor.lastrowid
    
    def get_scale_ins(self, parent_trade_id: int) -> List[Dict[str, Any]]:
        """Get all scale-ins for a parent trade"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trade_history_scale_ins WHERE parent_trade_id = ?
            """, (parent_trade_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def migrate_from_jsonl(self, jsonl_path: str):
        """Migrate existing trade history from JSONL file"""
        if not os.path.exists(jsonl_path):
            logger.warning(f"JSONL file not found: {jsonl_path}")
            return
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            imported = 0
            
            with open(jsonl_path, 'r') as f:
                for line in f:
                    try:
                        trade_data = json.loads(line.strip())
                        # Map JSONL fields to DB schema
                        mapped = {
                            "symbol": trade_data.get("symbol"),
                            "side": trade_data.get("side"),
                            "quantity": trade_data.get("quantity"),
                            "price": trade_data.get("price"),
                            "notional": trade_data.get("notional"),
                            "order_id": trade_data.get("order_id"),
                            "status": trade_data.get("status"),
                            "cycle_id": trade_data.get("cycle_id")
                        }
                        
                        cursor.execute("""
                            INSERT INTO trade_history_trades (
                                symbol, side, quantity, price, notional, order_id, status, cycle_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            mapped["symbol"], mapped["side"], mapped["quantity"],
                            mapped["price"], mapped["notional"], mapped["order_id"],
                            mapped["status"], mapped["cycle_id"]
                        ))
                        imported += 1
                    except json.JSONDecodeError:
                        continue
            
            conn.commit()
            logger.info(f"Migrated {imported} trades from {jsonl_path}")


# Global instance
_trade_history_db = None


def get_trade_history() -> TradeHistoryDB:
    """Get or create global trade history instance"""
    global _trade_history_db
    if _trade_history_db is None:
        _trade_history_db = TradeHistoryDB()
    return _trade_history_db


if __name__ == "__main__":
    # Example usage
    db = TradeHistoryDB()
    
    # Add a test trade
    db.add_trade({
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.01,
        "price": 50000.0,
        "notional": 500.0,
        "order_id": "test123",
        "status": "FILLED"
    })
    
    # Get stats
    stats = db.get_trade_stats()
    print(f"Trade stats: {stats}")
