#!/usr/bin/env python3
"""
Migration script to import historical data from JSON files into the new DB tables.

Usage:
    python scripts/migrate_historical_data.py
"""

import json
import os
import sys
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.repositories import insert_closed_position, insert_raw_signal
from state.models import ClosedPosition, RawSignal


def migrate_closed_positions():
    """Import closed positions from historical JSON files."""
    
    # Look for trade_state files in history
    history_dir = "logs/history"
    
    closed_positions = []
    
    # Try to find and parse trade_state files
    for filename in os.listdir(history_dir):
        if filename.startswith("trade_state_") and filename.endswith(".json"):
            filepath = os.path.join(history_dir, filename)
            print(f"Processing: {filepath}")
            
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            # Parse timestamp from filename (e.g., trade_state_20260301_165942.json)
            # This is the close_time for these positions
            ts_str = filename.replace("trade_state_", "").replace(".json", "")
            close_time = None
            try:
                # Parse as epoch ms
                close_time = int(ts_str)  
            except:
                # Fallback to current time
                close_time = int(datetime.now().timestamp() * 1000)
            
            # Each key in the JSON is a symbol with position data
            for symbol, pos_data in data.items():
                # Determine close_reason (assume MANUAL since positions are closed)
                close_reason = "MANUAL"  # or derive from tp1_hit/tp2_hit/sl_hit
                if pos_data.get("tp1_hit") or pos_data.get("tp2_hit"):
                    close_reason = "TP"
                elif pos_data.get("sl_hit"):
                    close_reason = "SL"
                
                cp = ClosedPosition(
                    symbol=symbol,
                    side=pos_data.get("side", "BUY"),
                    entry_price=pos_data.get("entry_price", 0),
                    exit_price=0,  # Not available in historical data
                    quantity=pos_data.get("size", 0),
                    pnl=0,  # Would need exit price to calculate
                    realized_pnl=0,
                    open_time=pos_data.get("open_time"),
                    close_time=close_time,
                    duration_minutes=None,
                    trade_reason="ML_SIGNAL",
                    close_reason=close_reason,
                    correlation_id=None,
                    metadata={
                        "tp1_hit": pos_data.get("tp1_hit", False),
                        "tp2_hit": pos_data.get("tp2_hit", False),
                        "sl_hit": pos_data.get("sl_hit", False),
                        "source_file": filename,
                    },
                )
                closed_positions.append(cp)
    
    # Insert into database
    count = 0
    for cp in closed_positions:
        try:
            insert_closed_position(cp)
            count += 1
            print(f"  Inserted: {cp.symbol} {cp.side} @ {cp.entry_price}")
        except Exception as e:
            print(f"  Error inserting {cp.symbol}: {e}")
    
    print(f"\n✅ Imported {count} closed positions")
    return count


def migrate_raw_signals():
    """Copy current last_signals to raw_signals for historical tracking."""
    from state.repositories import get_raw_signals, insert_raw_signal
    from state.repositories import get_last_signals
    
    # Get last_signals from DB
    # This is a simplified migration - in production you'd want to 
    # also import any historical signal data from JSON files
    
    # For now, we'll just note that raw_signals will be populated 
    # going forward by the trading system
    print("ℹ️  Raw signals will be populated going forward by the trading system")
    print("    (Historical signal import from JSON not implemented)")
    return 0


def main():
    print("=" * 60)
    print("Historical Data Migration")
    print("=" * 60)
    
    # Migrate closed positions
    print("\n--- Migrating Closed Positions ---")
    count1 = migrate_closed_positions()
    
    # Migrate raw signals
    print("\n--- Migrating Raw Signals ---")
    count2 = migrate_raw_signals()
    
    print("\n" + "=" * 60)
    print(f"✅ Migration complete!")
    print(f"   Closed positions: {count1}")
    print(f"   Raw signals: {count2}")
    print("=" * 60)


if __name__ == "__main__":
    main()
