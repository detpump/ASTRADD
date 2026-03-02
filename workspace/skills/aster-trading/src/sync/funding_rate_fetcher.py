# src/sync/funding_rate_fetcher.py
"""
Funding Rate Fetch & Storage.

This module fetches funding rates from the exchange API and stores them
in the database for later retrieval and analysis.
"""

import logging
import time
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class FundingRateFetcher:
    """
    Fetches and stores funding rates from the exchange.
    
    Funding rates are used for:
    - Position cost calculation
    - P&L estimation
    - Risk management decisions
    """
    
    # Binance API endpoint for funding rates
    FUNDING_RATE_ENDPOINT = "/fapi/v1/fundingRate"
    
    def __init__(self):
        self._cache: Dict[str, Dict] = {}  # symbol -> {rate, timestamp}
        self._cache_ttl_seconds = 60  # Cache TTL
    
    def fetch_and_store(self, symbols: List[str]) -> int:
        """
        Fetch funding rates for symbols and store in DB.
        
        Args:
            symbols: List of trading symbols (e.g., ['BTCUSDT', 'ETHUSDT'])
        
        Returns:
            Number of symbols successfully fetched and stored
        """
        from api.aster_api import public_get
        from state.db import get_connection
        
        stored_count = 0
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            for symbol in symbols:
                try:
                    # Fetch funding rate from API
                    params = {"symbol": symbol, "limit": 1}
                    data = public_get(self.FUNDING_RATE_ENDPOINT, params)
                    
                    if data and len(data) > 0:
                        rate_data = data[0]
                        
                        # Extract funding rate data
                        funding_rate = float(rate_data["fundingRate"])
                        funding_time = int(rate_data["fundingTime"])
                        next_funding_time = int(rate_data.get("nextFundingTime", 0))
                        
                        # Store in database
                        cur.execute(
                            """INSERT OR REPLACE INTO funding_rates 
                               (symbol, funding_rate, funding_time, next_funding_time, created_at)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                symbol,
                                funding_rate,
                                funding_time,
                                next_funding_time,
                                int(time.time() * 1000)
                            )
                        )
                        
                        # Update cache
                        self._cache[symbol] = {
                            "funding_rate": funding_rate,
                            "funding_time": funding_time,
                            "next_funding_time": next_funding_time,
                            "cached_at": time.time()
                        }
                        
                        stored_count += 1
                        logger.debug(
                            f"Stored funding rate for {symbol}: {funding_rate}, "
                            f"next funding at {datetime.fromtimestamp(next_funding_time / 1000)}"
                        )
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch funding rate for {symbol}: {e}")
            
            conn.commit()
        
        logger.info(f"Funding rates fetched and stored for {stored_count}/{len(symbols)} symbols")
        return stored_count
    
    def get_latest_rate(self, symbol: str) -> Optional[float]:
        """
        Get latest funding rate for symbol.
        
        First checks cache, then falls back to database.
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
        
        Returns:
            Latest funding rate, or 0.0 if not available
        """
        # Check cache first
        cached = self._cache.get(symbol)
        if cached:
            cache_age = time.time() - cached.get("cached_at", 0)
            if cache_age < self._cache_ttl_seconds:
                return cached["funding_rate"]
        
        # Fetch from database
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT funding_rate, funding_time, next_funding_time 
                   FROM funding_rates 
                   WHERE symbol = ? 
                   ORDER BY funding_time DESC 
                   LIMIT 1""",
                (symbol,)
            )
            row = cur.fetchone()
            
            if row:
                funding_rate = row[0]
                funding_time = row[1]
                next_funding_time = row[2]
                
                # Update cache
                self._cache[symbol] = {
                    "funding_rate": funding_rate,
                    "funding_time": funding_time,
                    "next_funding_time": next_funding_time,
                    "cached_at": time.time()
                }
                
                return funding_rate
        
        return 0.0
    
    def get_next_funding_time(self, symbol: str) -> Optional[int]:
        """
        Get next funding time for symbol.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Next funding time as Unix timestamp in milliseconds, or None
        """
        from state.db import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT next_funding_time 
                   FROM funding_rates 
                   WHERE symbol = ? 
                   ORDER BY funding_time DESC 
                   LIMIT 1""",
                (symbol,)
            )
            row = cur.fetchone()
            return row[0] if row else None
    
    def calculate_funding_cost(
        self,
        symbol: str,
        position_size: float,
        position_side: str
    ) -> float:
        """
        Calculate estimated funding cost for a position.
        
        Args:
            symbol: Trading symbol
            position_size: Position size (in base asset)
            position_side: 'LONG' or 'SHORT'
        
        Returns:
            Estimated funding cost per funding period (8 hours)
        """
        funding_rate = self.get_latest_rate(symbol)
        
        if funding_rate == 0.0:
            return 0.0
        
        # Funding is paid/received based on position side
        # Long positions pay/receive based on positive/negative rate
        cost = position_size * funding_rate
        
        # For short positions, cost is inverted
        if position_side.upper() == "SHORT":
            cost = -cost
        
        return cost
    
    def clear_cache(self):
        """Clear the in-memory cache."""
        self._cache.clear()
        logger.debug("Funding rate cache cleared")
    
    def get_all_cached_rates(self) -> Dict[str, float]:
        """
        Get all currently cached funding rates.
        
        Returns:
            Dictionary of symbol -> funding_rate
        """
        return {
            symbol: data["funding_rate"]
            for symbol, data in self._cache.items()
        }
