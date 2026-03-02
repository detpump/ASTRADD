#!/usr/bin/env python3
"""
Market Data Simulator for Testing.

This module provides realistic market data simulation for testing trading strategies
and risk management systems without connecting to real exchanges.
"""

import random
import math
import time
import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("market_simulator")


# =============================================================================
# Price Models
# =============================================================================

class PriceModel:
    """Base class for price movement models"""
    
    def next_price(self, current_price: float, timestamp: int) -> float:
        """Calculate next price"""
        raise NotImplementedError


class RandomWalkModel(PriceModel):
    """Random walk price model"""
    
    def __init__(self, volatility: float = 0.001):
        """
        Args:
            volatility: Standard deviation of price changes per step
        """
        self.volatility = volatility
    
    def next_price(self, current_price: float, timestamp: int) -> float:
        change = random.gauss(0, self.volatility)
        return current_price * (1 + change)


class MeanReversionModel(PriceModel):
    """Mean reversion price model"""
    
    def __init__(self, mean_price: float = 42000, reversion_speed: float = 0.1, volatility: float = 0.002):
        self.mean_price = mean_price
        self.reversion_speed = reversion_speed
        self.volatility = volatility
    
    def next_price(self, current_price: float, timestamp: int) -> float:
        # Pull towards mean
        reversion = (self.mean_price - current_price) / self.mean_price * self.reversion_speed
        # Add noise
        noise = random.gauss(0, self.volatility)
        return current_price * (1 + reversion + noise)


class MomentumModel(PriceModel):
    """Momentum/trending price model"""
    
    def __init__(self, trend_strength: float = 0.002, volatility: float = 0.001):
        self.trend_strength = trend_strength
        self.volatility = volatility
        self.current_trend = 1.0
    
    def next_price(self, current_price: float, timestamp: int) -> float:
        # Update trend slowly
        if random.random() < 0.1:
            self.current_trend = random.choice([-1, 1])
        
        trend = self.trend_strength * self.current_trend
        noise = random.gauss(0, self.volatility)
        return current_price * (1 + trend + noise)


class VolatileMarketModel(PriceModel):
    """High volatility price model for stress testing"""
    
    def __init__(self, volatility: float = 0.005):
        self.volatility = volatility
    
    def next_price(self, current_price: float, timestamp: int) -> float:
        # Occasional large moves
        if random.random() < 0.05:
            large_move = random.uniform(-0.03, 0.03)
            return current_price * (1 + large_move)
        
        change = random.gauss(0, self.volatility)
        return current_price * (1 + change)


class SidewaysMarketModel(PriceModel):
    """Range-bound sideways market"""
    
    def __init__(self, min_price: float = 40000, max_price: float = 44000, volatility: float = 0.001):
        self.min_price = min_price
        self.max_price = max_price
        self.volatility = volatility
    
    def next_price(self, current_price: float, timestamp: int) -> float:
        # Check bounds and reverse if needed
        if current_price < self.min_price:
            # Bounce up
            return current_price * (1 + abs(random.gauss(0, self.volatility)))
        elif current_price > self.max_price:
            # Bounce down
            return current_price * (1 - abs(random.gauss(0, self.volatility)))
        
        # Random walk with slight pull to center
        center = (self.min_price + self.max_price) / 2
        reversion = (center - current_price) / center * 0.001
        noise = random.gauss(0, self.volatility)
        
        return current_price * (1 + reversion + noise)


# =============================================================================
# Market Simulator
# =============================================================================

@dataclass
class MarketTick:
    """Single market tick data"""
    timestamp: int
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    bid: float
    ask: float


@dataclass 
class OrderBookLevel:
    """Order book price level"""
    price: float
    quantity: float


class MarketSimulator:
    """
    Main market data simulator that generates realistic price movements
    and order book data.
    """
    
    MODELS = {
        "random_walk": RandomWalkModel,
        "mean_reversion": MeanReversionModel,
        "momentum": MomentumModel,
        "volatile": VolatileMarketModel,
        "sideways": SidewaysMarketModel,
    }
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        initial_price: float = 42000.0,
        model: str = "random_walk",
        **model_kwargs
    ):
        self.symbol = symbol
        self.current_price = initial_price
        self.initial_price = initial_price
        
        # Get price model
        model_class = self.MODELS.get(model, RandomWalkModel)
        self.price_model = model_class(**model_kwargs)
        
        # Order book state
        self.spread_bps = 0.5  # Basis points spread
        self.order_book_depth = 20
        
        # Price history
        self.price_history: List[float] = [initial_price]
        self.tick_history: List[MarketTick] = []
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Running state
        self.running = False
        self.update_interval = 1.0  # seconds
        
        logger.info(f"MarketSimulator initialized: {symbol} @ {initial_price}, model={model}")
    
    def start(self):
        """Start background price updates"""
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"MarketSimulator started for {self.symbol}")
    
    def stop(self):
        """Stop background price updates"""
        self.running = False
        if hasattr(self, '_thread'):
            self._thread.join(timeout=2)
        logger.info(f"MarketSimulator stopped for {self.symbol}")
    
    def _run(self):
        """Background update loop"""
        while self.running:
            with self.lock:
                self._update_price()
            time.sleep(self.update_interval)
    
    def _update_price(self):
        """Update price using the model"""
        timestamp = int(time.time() * 1000)
        
        new_price = self.price_model.next_price(self.current_price, timestamp)
        
        # Create tick
        open_price = self.current_price
        high_price = max(open_price, new_price)
        low_price = min(open_price, new_price)
        
        # Calculate bid/ask
        mid_price = new_price
        spread = mid_price * (self.spread_bps / 10000)
        bid = mid_price - spread / 2
        ask = mid_price + spread / 2
        
        # Volume
        volume = random.uniform(100, 1000)
        
        tick = MarketTick(
            timestamp=timestamp,
            symbol=self.symbol,
            open=open_price,
            high=high_price,
            low=low_price,
            close=new_price,
            volume=volume,
            bid=bid,
            ask=ask
        )
        
        self.current_price = new_price
        self.price_history.append(new_price)
        self.tick_history.append(tick)
        
        # Keep history bounded
        if len(self.price_history) > 10000:
            self.price_history = self.price_history[-5000:]
        if len(self.tick_history) > 5000:
            self.tick_history = self.tick_history[-2500:]
    
    def get_current_price(self) -> float:
        """Get current price"""
        with self.lock:
            return self.current_price
    
    def get_order_book(self, depth: int = 10) -> Tuple[List[OrderBookLevel], List[OrderBookLevel]]:
        """
        Get simulated order book.
        
        Returns:
            Tuple of (bids, asks) where each is a list of OrderBookLevel
        """
        with self.lock:
            mid = self.current_price
            spread = mid * (self.spread_bps / 10000)
            
            bids = []
            asks = []
            
            for i in range(depth):
                # Bids (buy orders) below mid
                bid_price = mid - spread/2 - (i * spread * 0.5)
                bid_qty = random.uniform(0.1, 5.0) * (1 - i * 0.05)
                bids.append(OrderBookLevel(price=bid_price, quantity=bid_qty))
                
                # Asks (sell orders) above mid
                ask_price = mid + spread/2 + (i * spread * 0.5)
                ask_qty = random.uniform(0.1, 5.0) * (1 - i * 0.05)
                asks.append(OrderBookLevel(price=ask_price, quantity=ask_qty))
            
            return bids, asks
    
    def get_klines(
        self,
        interval: str = "1m",
        limit: int = 100
    ) -> List[List]:
        """
        Get kline (candlestick) data.
        
        Args:
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles to return
            
        Returns:
            List of kline data in exchange format
        """
        with self.lock:
            # Determine interval in milliseconds
            interval_ms = {
                "1m": 60000,
                "5m": 300000,
                "15m": 900000,
                "1h": 3600000,
                "4h": 14400000,
                "1d": 86400000
            }.get(interval, 60000)
            
            candles = []
            now = int(time.time() * 1000)
            
            # Generate candles from history
            history_len = len(self.tick_history)
            
            if history_len == 0:
                # Generate from price history
                for i in range(limit):
                    ts = now - (limit - i) * interval_ms
                    price = self.price_history[min(i, len(self.price_history) - 1)] if self.price_history else self.initial_price
                    
                    candles.append([
                        ts,
                        str(price),
                        str(price * 1.001),
                        str(price * 0.999),
                        str(price),
                        str(random.uniform(100, 1000)),
                        ts + interval_ms,
                    ])
            else:
                # Use tick history
                for i in range(limit):
                    idx = history_len - limit + i
                    if idx < 0:
                        idx = 0
                    
                    tick = self.tick_history[idx]
                    
                    candles.append([
                        tick.timestamp,
                        str(tick.open),
                        str(tick.high),
                        str(tick.low),
                        str(tick.close),
                        str(tick.volume),
                        tick.timestamp + interval_ms,
                    ])
            
            return candles
    
    def simulate_market_event(self, event_type: str):
        """
        Simulate special market events.
        
        Args:
            event_type: Type of event (spike, crash, flash_crash, gap)
        """
        with self.lock:
            if event_type == "spike":
                # Sudden price increase
                self.current_price *= random.uniform(1.02, 0.05)
            elif event_type == "crash":
                # Sudden price decrease
                self.current_price *= random.uniform(0.95, 0.90)
            elif event_type == "flash_crash":
                # Quick drop and recovery
                drop = random.uniform(0.10, 0.20)
                self.current_price *= (1 - drop)
                # Recovery will happen in next updates
            elif event_type == "gap":
                # Price gap (like after market open)
                gap = random.uniform(-0.05, 0.05)
                self.current_price *= (1 + gap)
            
            self.price_history.append(self.current_price)


# =============================================================================
# Multi-Symbol Simulator
# =============================================================================

class MultiSymbolSimulator:
    """Manages multiple market simulators for different symbols"""
    
    def __init__(self):
        self.simulators: Dict[str, MarketSimulator] = {}
        self.lock = threading.Lock()
    
    def add_symbol(
        self,
        symbol: str,
        initial_price: float = 42000.0,
        model: str = "random_walk",
        **model_kwargs
    ) -> MarketSimulator:
        """Add a new symbol simulator"""
        with self.lock:
            sim = MarketSimulator(symbol, initial_price, model, **model_kwargs)
            self.simulators[symbol] = sim
            return sim
    
    def get_simulator(self, symbol: str) -> Optional[MarketSimulator]:
        """Get simulator for a symbol"""
        return self.simulators.get(symbol)
    
    def start_all(self):
        """Start all simulators"""
        for sim in self.simulators.values():
            sim.start()
    
    def stop_all(self):
        """Stop all simulators"""
        for sim in self.simulators.values():
            sim.stop()


# =============================================================================
# Test Utilities
# =============================================================================

def create_test_scenario(scenario_name: str) -> MarketSimulator:
    """
    Create a market simulator with preset scenario.
    
    Args:
        scenario_name: Name of scenario (bull, bear, volatile, sideways)
        
    Returns:
        Configured MarketSimulator
    """
    scenarios = {
        "bull": {
            "model": "momentum",
            "model_kwargs": {"trend_strength": 0.003, "volatility": 0.001},
            "initial_price": 40000
        },
        "bear": {
            "model": "momentum", 
            "model_kwargs": {"trend_strength": -0.003, "volatility": 0.001},
            "initial_price": 44000
        },
        "volatile": {
            "model": "volatile",
            "model_kwargs": {"volatility": 0.008},
            "initial_price": 42000
        },
        "sideways": {
            "model": "sideways",
            "model_kwargs": {"min_price": 40000, "max_price": 44000, "volatility": 0.001},
            "initial_price": 42000
        },
    }
    
    config = scenarios.get(scenario_name, scenarios["volatile"])
    
    return MarketSimulator(
        symbol="BTCUSDT",
        **config
    )


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Create a volatile market simulator
    sim = create_test_scenario("volatile")
    
    print("Market Simulator Demo")
    print("=" * 50)
    
    # Get some initial prices
    for _ in range(5):
        time.sleep(0.1)
        print(f"Price: {sim.get_current_price():.2f}")
    
    # Get order book
    bids, asks = sim.get_order_book(5)
    print("\nOrder Book (Top 5 levels):")
    print("Bids:")
    for level in bids[:5]:
        print(f"  {level.price:.2f} x {level.quantity:.4f}")
    print("Asks:")
    for level in asks[:5]:
        print(f"  {level.price:.2f} x {level.quantity:.4f}")
    
    # Simulate a flash crash
    print("\nSimulating flash crash...")
    sim.simulate_market_event("flash_crash")
    print(f"Price after crash: {sim.get_current_price():.2f}")
    
    sim.stop()
    print("\nDemo complete.")
