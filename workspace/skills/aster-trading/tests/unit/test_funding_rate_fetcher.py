#!/usr/bin/env python3
"""
Unit tests for FundingRateFetcher.

Tests funding rate fetching, caching, and calculation.
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.sync.funding_rate_fetcher import FundingRateFetcher


class TestFundingRateFetcher:
    """Test cases for FundingRateFetcher class."""
    
    @pytest.fixture
    def fetcher(self):
        """Create a FundingRateFetcher instance."""
        return FundingRateFetcher()
    
    # =========================================================================
    # Cache Tests
    # =========================================================================
    
    def test_cache_initially_empty(self, fetcher):
        """Test that cache starts empty."""
        assert len(fetcher._cache) == 0
    
    def test_clear_cache(self, fetcher):
        """Test clearing the cache."""
        fetcher._cache["BTCUSDT"] = {"funding_rate": 0.0001, "cached_at": 123456}
        fetcher.clear_cache()
        
        assert len(fetcher._cache) == 0
    
    def test_get_all_cached_rates(self, fetcher):
        """Test getting all cached rates."""
        fetcher._cache["BTCUSDT"] = {"funding_rate": 0.0001, "cached_at": 123456}
        fetcher._cache["ETHUSDT"] = {"funding_rate": 0.0002, "cached_at": 123456}
        
        rates = fetcher.get_all_cached_rates()
        
        assert rates["BTCUSDT"] == 0.0001
        assert rates["ETHUSDT"] == 0.0002
    
    # =========================================================================
    # Funding Rate Calculation Tests
    # =========================================================================
    
    def test_calculate_funding_cost_long_position(self, fetcher):
        """Test funding cost calculation for long position."""
        with patch.object(fetcher, 'get_latest_rate', return_value=0.0001):
            cost = fetcher.calculate_funding_cost("BTCUSDT", 1.0, "LONG")
            
            assert cost == 0.0001  # 1.0 * 0.0001
    
    def test_calculate_funding_cost_short_position(self, fetcher):
        """Test funding cost calculation for short position."""
        with patch.object(fetcher, 'get_latest_rate', return_value=0.0001):
            cost = fetcher.calculate_funding_cost("BTCUSDT", 1.0, "SHORT")
            
            assert cost == -0.0001  # Inverted for short
    
    def test_calculate_funding_cost_zero_rate(self, fetcher):
        """Test funding cost calculation when rate is zero."""
        with patch.object(fetcher, 'get_latest_rate', return_value=0.0):
            cost = fetcher.calculate_funding_cost("BTCUSDT", 1.0, "LONG")
            
            assert cost == 0.0
    
    def test_calculate_funding_cost_case_insensitive(self, fetcher):
        """Test funding cost calculation is case insensitive for side."""
        with patch.object(fetcher, 'get_latest_rate', return_value=0.0001):
            cost_lower = fetcher.calculate_funding_cost("BTCUSDT", 1.0, "long")
            cost_upper = fetcher.calculate_funding_cost("BTCUSDT", 1.0, "LONG")
            
            assert cost_lower == cost_upper
    
    # =========================================================================
    # Fetch and Store Tests (Mocked)
    # =========================================================================
    
    @patch('api.aster_api.public_get')
    @patch('state.db.get_connection')
    def test_fetch_and_store_success(self, mock_conn, mock_public_get, fetcher):
        """Test successful funding rate fetch and store."""
        # Setup mocks
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        mock_public_get.return_value = [{
            "fundingRate": "0.0001",
            "fundingTime": 1700000000000,
            "nextFundingTime": 1700035200000
        }]
        
        result = fetcher.fetch_and_store(["BTCUSDT"])
        
        assert result == 1
        assert "BTCUSDT" in fetcher._cache
    
    @patch('api.aster_api.public_get')
    @patch('state.db.get_connection')
    def test_fetch_and_store_multiple_symbols(self, mock_conn, mock_public_get, fetcher):
        """Test fetching funding rates for multiple symbols."""
        # Setup mocks
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        # Return list with two items regardless of symbol
        mock_public_get.return_value = [
            {"fundingRate": "0.0001", "fundingTime": 1700000000000, "nextFundingTime": 1700035200000}
        ]
        
        result = fetcher.fetch_and_store(["BTCUSDT", "ETHUSDT"])
        
        # Each call returns 1 result, so 2 symbols = 2 results
        assert result == 2
        assert "BTCUSDT" in fetcher._cache
        assert "ETHUSDT" in fetcher._cache
    
    @patch('api.aster_api.public_get')
    @patch('state.db.get_connection')
    def test_fetch_and_store_api_failure(self, mock_conn, mock_public_get, fetcher):
        """Test handling API failure during fetch."""
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        # API returns empty or raises exception
        mock_public_get.side_effect = Exception("API Error")
        
        result = fetcher.fetch_and_store(["BTCUSDT"])
        
        # Should return 0 and not crash
        assert result == 0
    
    @patch('api.aster_api.public_get')
    @patch('state.db.get_connection')
    def test_fetch_and_store_empty_response(self, mock_conn, mock_public_get, fetcher):
        """Test handling empty API response."""
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        mock_public_get.return_value = []
        
        result = fetcher.fetch_and_store(["BTCUSDT"])
        
        assert result == 0
    
    # =========================================================================
    # Get Latest Rate Tests (Mocked)
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_get_latest_rate_from_cache(self, mock_conn, fetcher):
        """Test getting rate from cache."""
        import time
        fetcher._cache["BTCUSDT"] = {
            "funding_rate": 0.0001,
            "funding_time": 1700000000000,
            "next_funding_time": 1700035200000,
            "cached_at": time.time()  # Fresh
        }
        
        result = fetcher.get_latest_rate("BTCUSDT")
        
        assert result == 0.0001
        mock_conn.assert_not_called()  # Should not hit DB
    
    @patch('state.db.get_connection')
    def test_get_latest_rate_from_db(self, mock_conn, fetcher):
        """Test getting rate from database when cache miss."""
        import time
        
        # Empty cache - should check DB
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (0.0001, 1700000000000, 1700035200000)
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = fetcher.get_latest_rate("BTCUSDT")
        
        assert result == 0.0001
        # Should have cached after DB fetch
        assert "BTCUSDT" in fetcher._cache
    
    @patch('state.db.get_connection')
    def test_get_latest_rate_not_found(self, mock_conn, fetcher):
        """Test getting rate when not found."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = fetcher.get_latest_rate("UNKNOWN")
        
        assert result == 0.0
    
    # =========================================================================
    # Get Next Funding Time Tests (Mocked)
    # =========================================================================
    
    @patch('state.db.get_connection')
    def test_get_next_funding_time_found(self, mock_conn, fetcher):
        """Test getting next funding time."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1700035200000,)
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = fetcher.get_next_funding_time("BTCUSDT")
        
        assert result == 1700035200000
    
    @patch('state.db.get_connection')
    def test_get_next_funding_time_not_found(self, mock_conn, fetcher):
        """Test getting next funding time when not found."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = fetcher.get_next_funding_time("BTCUSDT")
        
        assert result is None
    
    # =========================================================================
    # Cache TTL Tests
    # =========================================================================
    
    def test_cache_ttl_default(self, fetcher):
        """Test default cache TTL."""
        assert fetcher._cache_ttl_seconds == 60
    
    @patch('state.db.get_connection')
    def test_cache_expired_after_ttl(self, mock_conn, fetcher):
        """Test that cache expires after TTL."""
        import time
        
        # Set cache with old timestamp
        fetcher._cache["BTCUSDT"] = {
            "funding_rate": 0.0001,
            "funding_time": 1700000000000,
            "next_funding_time": 1700035200000,
            "cached_at": time.time() - 100  # Old cache
        }
        
        # Setup DB mock for when cache expires
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (0.0002, 1700000000000, 1700035200000)
        
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_conn.return_value)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur
        
        result = fetcher.get_latest_rate("BTCUSDT")
        
        # Should return DB value (0.0002) after cache expires
        assert result == 0.0002
    
    # =========================================================================
    # Edge Cases
    # =========================================================================
    
    def test_calculate_funding_cost_zero_position(self, fetcher):
        """Test funding cost calculation with zero position size."""
        with patch.object(fetcher, 'get_latest_rate', return_value=0.0001):
            cost = fetcher.calculate_funding_cost("BTCUSDT", 0, "LONG")
            
            assert cost == 0.0
    
    def test_calculate_funding_cost_large_position(self, fetcher):
        """Test funding cost calculation with large position."""
        with patch.object(fetcher, 'get_latest_rate', return_value=0.0001):
            cost = fetcher.calculate_funding_cost("BTCUSDT", 1000.0, "LONG")
            
            assert cost == 0.1  # 1000 * 0.0001
    
    def test_get_all_cached_rates_empty(self, fetcher):
        """Test getting all cached rates when cache is empty."""
        rates = fetcher.get_all_cached_rates()
        
        assert rates == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
