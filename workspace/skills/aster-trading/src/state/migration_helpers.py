#!/usr/bin/env python3
"""
Migration helpers for database schema migrations.

This module provides functions to:
- Check if migration is needed
- Apply migration
- Rollback migration if needed

Based on DATA_ARCHITECTURE_DETAILED_V3.md (Section 2)
"""

import logging
import os
import sqlite3
from typing import List, Optional, Tuple
from contextlib import contextmanager

logger = logging.getLogger("aster.migration")


# Migration tracking table name
MIGRATIONS_TABLE = "schema_migrations"


def get_migrations_dir() -> str:
    """Get the path to the migrations directory."""
    # This will be relative to the src/state directory
    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    return migrations_dir


@contextmanager
def get_migration_connection(db_path: str):
    """Get a database connection for migration operations."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_current_version(db_path: str) -> int:
    """
    Get the current schema version from the database.
    
    Returns 0 if no migrations have been applied.
    """
    try:
        with get_migration_connection(db_path) as conn:
            cur = conn.cursor()
            # Check if migrations table exists
            cur.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name=?
            """, (MIGRATIONS_TABLE,))
            
            if not cur.fetchone():
                return 0
            
            # Get the latest applied version
            cur.execute(f"SELECT MAX(version) as version FROM {MIGRATIONS_TABLE}")
            result = cur.fetchone()
            return result["version"] if result and result["version"] is not None else 0
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not determine schema version: {e}")
        return 0


def get_applied_migrations(db_path: str) -> List[Tuple[int, str, int]]:
    """
    Get all applied migrations.
    
    Returns a list of tuples: (version, name, applied_at)
    """
    try:
        with get_migration_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT version, name, applied_at 
                FROM {MIGRATIONS_TABLE}
                ORDER BY version
            """)
            return [(row["version"], row["name"], row["applied_at"]) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def check_migration_needed(db_path: str) -> Tuple[bool, int, int]:
    """
    Check if migration is needed.
    
    Returns:
        (needs_migration, current_version, target_version)
    """
    target_version = 1  # Current target version
    current_version = get_current_version(db_path)
    
    needs_migration = current_version < target_version
    return needs_migration, current_version, target_version


def check_table_exists(db_path: str, table_name: str) -> bool:
    """Check if a table exists in the database."""
    with get_migration_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name=?
        """, (table_name,))
        return cur.fetchone() is not None


def check_column_exists(db_path: str, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    with get_migration_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cur.fetchall()]
        return column_name in columns


def apply_migration(db_path: str, migration_sql: str) -> bool:
    """
    Apply a migration SQL script to the database.
    
    Args:
        db_path: Path to the database file
        migration_sql: SQL script to execute
        
    Returns:
        True if migration was successful, False otherwise
    """
    try:
        with get_migration_connection(db_path) as conn:
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys=ON")
            
            # Execute the migration script
            conn.executescript(migration_sql)
            conn.commit()
            
            logger.info("Migration applied successfully")
            return True
            
    except sqlite3.Error as e:
        logger.error(f"Migration failed: {e}")
        return False


def load_migration_sql(version: int) -> Optional[str]:
    """
    Load migration SQL from file.
    
    Args:
        version: Migration version number
        
    Returns:
        SQL content or None if file not found
    """
    migrations_dir = get_migrations_dir()
    migration_file = os.path.join(migrations_dir, f"{version:03d}_add_v3_schema.sql")
    
    if os.path.exists(migration_file):
        with open(migration_file, 'r') as f:
            return f.read()
    
    logger.warning(f"Migration file not found: {migration_file}")
    return None


def run_migration(db_path: str) -> Tuple[bool, str]:
    """
    Run the V3 schema migration.
    
    Args:
        db_path: Path to the database file
        
    Returns:
        (success, message)
    """
    needs_migration, current_version, target_version = check_migration_needed(db_path)
    
    if not needs_migration:
        return True, f"Database already at version {current_version}"
    
    logger.info(f"Running migration from version {current_version} to {target_version}")
    
    # Load migration SQL
    migration_sql = load_migration_sql(target_version)
    if not migration_sql:
        return False, f"Could not load migration SQL for version {target_version}"
    
    # Apply migration
    if apply_migration(db_path, migration_sql):
        new_version = get_current_version(db_path)
        return True, f"Migration successful. Database now at version {new_version}"
    else:
        return False, "Migration failed"


def create_rollback_script(db_path: str, version: int) -> str:
    """
    Create a rollback SQL script for the given version.
    
    Args:
        db_path: Path to the database file
        version: Migration version to rollback
        
    Returns:
        SQL rollback script
    """
    # For V3 schema, rollback involves dropping all new tables
    rollback_sql = f"""
-- Rollback for migration version {version}
-- This script drops all tables created by the V3 migration

-- Drop views first (they depend on tables)
DROP VIEW IF EXISTS v_sync_health;
DROP VIEW IF EXISTS v_risk_dashboard;
DROP VIEW IF EXISTS v_position_pnl;
DROP VIEW IF EXISTS v_active_positions;

-- Drop Layer 4 tables
DROP TABLE IF EXISTS sync_metrics;
DROP TABLE IF EXISTS system_components;
DROP TABLE IF EXISTS risk_events;
DROP TABLE IF EXISTS risk_states;

-- Drop Layer 3 tables
DROP TABLE IF EXISTS bracket_orders;
DROP TABLE IF EXISTS position_closes;
DROP TABLE IF EXISTS position_entries;
DROP TABLE IF EXISTS positions_v3;

-- Drop Layer 2 tables
DROP TABLE IF EXISTS event_processing_errors;
DROP TABLE IF EXISTS scale_in_conditions_log;
DROP TABLE IF EXISTS bracket_state_log;
DROP TABLE IF EXISTS events;

-- Drop Layer 1 tables
DROP TABLE IF EXISTS raw_account_snapshots;
DROP TABLE IF EXISTS raw_order_snapshots;
DROP TABLE IF EXISTS raw_position_snapshots;

-- Drop Additional tables
DROP TABLE IF EXISTS funding_rates;
DROP TABLE IF EXISTS sync_batches;

-- Drop migrations table
DROP TABLE IF EXISTS {MIGRATIONS_TABLE};
"""
    return rollback_sql


def rollback_migration(db_path: str, version: int = 1) -> Tuple[bool, str]:
    """
    Rollback a migration.
    
    Args:
        db_path: Path to the database file
        version: Migration version to rollback (default: 1)
        
    Returns:
        (success, message)
    """
    current_version = get_current_version(db_path)
    
    if current_version < version:
        return False, f"Cannot rollback version {version}: database is at version {current_version}"
    
    logger.warning(f"Rolling back migration version {version}")
    
    # Create and execute rollback script
    rollback_sql = create_rollback_script(db_path, version)
    
    try:
        with get_migration_connection(db_path) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(rollback_sql)
            conn.commit()
            
            logger.info(f"Rollback successful for version {version}")
            return True, f"Rollback successful. Database now at version 0"
            
    except sqlite3.Error as e:
        logger.error(f"Rollback failed: {e}")
        return False, f"Rollback failed: {e}"


def verify_migration(db_path: str) -> Tuple[bool, List[str]]:
    """
    Verify that the migration was applied correctly.
    
    Args:
        db_path: Path to the database file
        
    Returns:
        (is_valid, list_of_issues)
    """
    issues = []
    
    # Required tables for Layer 1
    layer1_tables = [
        "raw_position_snapshots",
        "raw_order_snapshots", 
        "raw_account_snapshots"
    ]
    
    # Required tables for Layer 2
    layer2_tables = [
        "events",
        "bracket_state_log",
        "scale_in_conditions_log",
        "event_processing_errors"
    ]
    
    # Required tables for Layer 3
    layer3_tables = [
        "positions_v3",
        "position_entries",
        "position_closes",
        "bracket_orders"
    ]
    
    # Required tables for Layer 4
    layer4_tables = [
        "risk_states",
        "risk_events",
        "system_components",
        "sync_metrics"
    ]
    
    # Additional tables
    additional_tables = [
        "sync_batches",
        "funding_rates",
        MIGRATIONS_TABLE
    ]
    
    all_tables = layer1_tables + layer2_tables + layer3_tables + layer4_tables + additional_tables
    
    for table in all_tables:
        if not check_table_exists(db_path, table):
            issues.append(f"Missing table: {table}")
    
    # Check for required views
    views = ["v_active_positions", "v_position_pnl", "v_risk_dashboard", "v_sync_health"]
    for view in views:
        with get_migration_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='view' AND name=?
            """, (view,))
            if not cur.fetchone():
                issues.append(f"Missing view: {view}")
    
    is_valid = len(issues) == 0
    return is_valid, issues


def get_migration_status(db_path: str) -> dict:
    """
    Get the current migration status.
    
    Returns:
        Dictionary with migration status information
    """
    needs_migration, current_version, target_version = check_migration_needed(db_path)
    applied_migrations = get_applied_migrations(db_path)
    
    status = {
        "needs_migration": needs_migration,
        "current_version": current_version,
        "target_version": target_version,
        "applied_migrations": applied_migrations,
    }
    
    if current_version > 0:
        is_valid, issues = verify_migration(db_path)
        status["is_valid"] = is_valid
        status["issues"] = issues
    
    return status


# CLI interface for running migrations
def main():
    """CLI for running migrations."""
    import sys
    
    # Get database path
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        # Default to logs/aster.db
        from paths import DATABASE_FILE
        db_path = DATABASE_FILE
    
    if len(sys.argv) > 2:
        command = sys.argv[2]
    else:
        command = "status"
    
    if command == "status":
        status = get_migration_status(db_path)
        print(f"Current version: {status['current_version']}")
        print(f"Target version: {status['target_version']}")
        print(f"Needs migration: {status['needs_migration']}")
        
        if status.get('is_valid') is not None:
            print(f"Valid: {status['is_valid']}")
            if status.get('issues'):
                print("Issues:")
                for issue in status['issues']:
                    print(f"  - {issue}")
        
    elif command == "migrate":
        success, message = run_migration(db_path)
        print(message)
        sys.exit(0 if success else 1)
        
    elif command == "rollback":
        success, message = rollback_migration(db_path)
        print(message)
        sys.exit(0 if success else 1)
        
    elif command == "verify":
        is_valid, issues = verify_migration(db_path)
        if is_valid:
            print("Migration verification passed")
        else:
            print("Migration verification failed:")
            for issue in issues:
                print(f"  - {issue}")
        sys.exit(0 if is_valid else 1)
        
    else:
        print(f"Unknown command: {command}")
        print("Usage: migration_helpers.py [db_path] [status|migrate|rollback|verify]")
        sys.exit(1)


if __name__ == "__main__":
    main()
