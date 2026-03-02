#!/usr/bin/env python3
"""
Dynamic Risk Adjustment Scheduler

This script periodically adjusts risk parameters based on market conditions.
It reads fresh market data (volatility, volume), calculates appropriate
risk multipliers, and updates the risk_config.json file.

Run via macOS cron:
    */5 * * * * /Users/FIRMAS/.openclaw/.venv/bin/python /Users/FIRMAS/.openclaw/workspace/skills/aster-trading/src/scheduler/risk_scheduler.py >> /Users/FIRMAS/.openclaw/workspace/skills/aster-trading/logs/risk_scheduler.log 2>&1

Or manually:
    python3 risk_scheduler.py
"""

import sys
import os
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent.parent
SRC_DIR = SKILL_DIR / "src"
CONFIG_DIR = SKILL_DIR / "config"
LOG_DIR = SKILL_DIR / "logs"

# Add src to path for imports
sys.path.insert(0, str(SRC_DIR))

# Configure logging
LOG_FILE = LOG_DIR / "risk_scheduler.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Risk configuration paths
RISK_CONFIG_FILE = CONFIG_DIR / "risk_config.json"

# Market data parameters
VOLATILITYlookback = 50  # Number of klines to analyze
VOLATILITY_THRESHOLD_LOW = 0.02   # 2% - below this is calm market
VOLATILITY_THRESHOLD_HIGH = 0.05  # 5% - above this is volatile market

# Risk adjustment multipliers
VOLATILE_MARKET_LEVERAGE_MULT = 0.5   # Reduce leverage by 50% in volatile markets
CALM_MARKET_LEVERAGE_MULT = 1.2      # Increase leverage by 20% in calm markets
VOLATILE_NOTIONAL_MULT = 0.5          # Reduce notional by 50% in volatile markets
CALM_NOTIONAL_MULT = 1.3              # Increase notional by 30% in calm markets
VOLATILE_SL_WIDER = 1.5               # Wider stop loss in volatile markets
CALM_SL_TIGHTER = 0.8                 # Tighter stop loss in calm markets


def load_risk_config() -> dict:
    """Load the current risk configuration."""
    try:
        with open(RISK_CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load risk config: {e}")
        return {}


def save_risk_config(config: dict) -> bool:
    """Save the updated risk configuration."""
    try:
        with open(RISK_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save risk config: {e}")
        return False


def calculate_volatility(prices: list) -> float:
    """
    Calculate volatility as the standard deviation of price returns.
    Returns a float representing the percentage volatility.
    """
    if len(prices) < 2:
        return 0.0
    
    # Calculate returns
    returns = []
    for i in range(1, len(prices)):
        if prices[i-1] > 0:
            ret = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(ret)
    
    if not returns:
        return 0.0
    
    # Calculate standard deviation
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    std_dev = variance ** 0.5
    
    return std_dev


def calculate_avg_volume(volumes: list) -> float:
    """Calculate average volume."""
    if not volumes:
        return 0.0
    return sum(volumes) / len(volumes)


async def fetch_market_data(symbol: str) -> dict:
    """
    Fetch market data for a symbol to calculate volatility and volume.
    Returns dict with volatility, avg_volume, current_price.
    """
    try:
        # Import here to avoid issues if not in path
        from api.aster_api import get_klines
        import asyncio
        
        # Fetch klines (synchronous wrapper)
        loop = asyncio.get_event_loop()
        
        # Use threading to avoid blocking
        klines = await asyncio.wait_for(
            asyncio.to_thread(get_klines, symbol, '1m', VOLATILITYlookback),
            timeout=30.0
        )
        
        if not klines:
            logger.warning(f"No klines fetched for {symbol}")
            return None
        
        # Extract prices and volumes
        prices = [float(k['close']) for k in klines]
        volumes = [float(k['volume']) for k in klines]
        
        current_price = prices[-1] if prices else 0.0
        
        # Calculate metrics
        volatility = calculate_volatility(prices)
        avg_volume = calculate_avg_volume(volumes)
        
        return {
            'symbol': symbol,
            'volatility': volatility,
            'avg_volume': avg_volume,
            'current_price': current_price,
            'klines_count': len(klines)
        }
        
    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching market data for {symbol}")
        return None
    except Exception as e:
        logger.error(f"Error fetching market data for {symbol}: {e}")
        return None


def calculate_risk_multiplier(volatility: float) -> tuple:
    """
    Calculate risk adjustment multipliers based on volatility.
    
    Returns:
        tuple: (leverage_mult, notional_mult, sl_mult, risk_profile)
    """
    if volatility < VOLATILITY_THRESHOLD_LOW:
        # Calm market - can be more aggressive
        return (
            CALM_MARKET_LEVERAGE_MULT,   # 1.2x
            CALM_NOTIONAL_MULT,          # 1.3x
            CALM_SL_TIGHTER,             # 0.8x (tighter stops)
            "aggressive"
        )
    elif volatility > VOLATILITY_THRESHOLD_HIGH:
        # Volatile market - be more conservative
        return (
            VOLATILE_MARKET_LEVERAGE_MULT,   # 0.5x
            VOLATILE_NOTIONAL_MULT,           # 0.5x
            VOLATILE_SL_WIDER,                # 1.5x (wider stops)
            "conservative"
        )
    else:
        # Normal market - use defaults
        return (1.0, 1.0, 1.0, "normal")


def adjust_symbol_params(symbol_config: dict, market_data: dict, leverage_mult: float, 
                        notional_mult: float, sl_mult: float) -> dict:
    """
    Adjust a symbol's risk parameters based on market conditions.
    """
    updated = symbol_config.copy()
    
    # Adjust leverage
    current_leverage = symbol_config.get('max_leverage', 5)
    new_leverage = int(current_leverage * leverage_mult)
    new_leverage = max(1, min(new_leverage, 125))  # Binance max is 125x
    updated['max_leverage'] = new_leverage
    
    # Adjust notionals
    current_min = symbol_config.get('min_notional_usdt', 5.0)
    current_max = symbol_config.get('max_notional_usdt', 15.0)
    
    # Scale max notional based on multiplier
    new_max = round(current_max * notional_mult, 1)
    new_max = max(current_min * 2, new_max)  # Ensure max is at least 2x min
    updated['max_notional_usdt'] = new_max
    
    # Adjust SL range
    sl_min = symbol_config.get('sl_pct_min', 0.7)
    sl_max = symbol_config.get('sl_pct_max', 3.0)
    
    # Wider stops in volatile, tighter in calm
    new_sl_min = round(sl_min * sl_mult, 2)
    new_sl_max = round(sl_max * sl_mult, 2)
    
    # Clamp values
    new_sl_min = max(0.3, min(new_sl_min, 5.0))
    new_sl_max = max(1.0, min(new_sl_max, 10.0))
    
    updated['sl_pct_min'] = new_sl_min
    updated['sl_pct_max'] = new_sl_max
    
    return updated


async def run_risk_adjustment() -> bool:
    """
    Main function to run risk adjustment.
    Returns True if successful, False otherwise.
    """
    logger.info("=" * 60)
    logger.info("Starting dynamic risk adjustment...")
    logger.info("=" * 60)
    
    # Load current config
    config = load_risk_config()
    if not config:
        logger.error("Could not load risk configuration")
        return False
    
    # Get current mode
    current_profile = config.get('mode', {}).get('risk_profile', 'normal')
    logger.info(f"Current risk profile: {current_profile}")
    
    # Get symbols to analyze
    symbols = list(config.get('symbols', {}).keys())
    if not symbols:
        logger.warning("No symbols configured")
        return False
    
    logger.info(f"Analyzing {len(symbols)} symbols: {symbols}")
    
    # Track changes
    changes = []
    overall_profile = "normal"
    
    # Fetch and analyze each symbol
    for symbol in symbols:
        logger.info(f"\n--- Analyzing {symbol} ---")
        
        # Get market data
        market_data = await fetch_market_data(symbol)
        
        if not market_data:
            logger.warning(f"Skipping {symbol} - no market data")
            continue
        
        logger.info(f"  Volatility: {market_data['volatility']*100:.2f}%")
        logger.info(f"  Avg Volume: {market_data['avg_volume']:.2f}")
        logger.info(f"  Current Price: ${market_data['current_price']:.2f}")
        
        # Calculate risk multipliers
        leverage_mult, notional_mult, sl_mult, profile = calculate_risk_multiplier(
            market_data['volatility']
        )
        
        logger.info(f"  Risk multipliers: leverage={leverage_mult}x, notional={notional_mult}x, SL={sl_mult}x")
        logger.info(f"  Market condition: {profile}")
        
        # Update overall profile based on worst case
        if profile == "conservative":
            overall_profile = "conservative"
        elif profile == "aggressive" and overall_profile == "normal":
            overall_profile = "aggressive"
        
        # Get current symbol config
        symbol_config = config.get('symbols', {}).get(symbol, {})
        if not symbol_config:
            continue
        
        # Store original values for comparison
        orig_leverage = symbol_config.get('max_leverage')
        orig_max_notional = symbol_config.get('max_notional_usdt')
        orig_sl_min = symbol_config.get('sl_pct_min')
        orig_sl_max = symbol_config.get('sl_pct_max')
        
        # Adjust parameters
        updated_config = adjust_symbol_params(
            symbol_config, market_data, leverage_mult, notional_mult, sl_mult
        )
        
        # Update config
        config['symbols'][symbol] = updated_config
        
        # Log changes
        change = {
            'symbol': symbol,
            'volatility': f"{market_data['volatility']*100:.2f}%",
            'profile': profile,
            'changes': {}
        }
        
        if orig_leverage != updated_config.get('max_leverage'):
            change['changes']['max_leverage'] = f"{orig_leverage} -> {updated_config.get('max_leverage')}"
        
        if orig_max_notional != updated_config.get('max_notional_usdt'):
            change['changes']['max_notional_usdt'] = f"{orig_max_notional} -> {updated_config.get('max_notional_usdt')}"
        
        if orig_sl_min != updated_config.get('sl_pct_min'):
            change['changes']['sl_pct_min'] = f"{orig_sl_min} -> {updated_config.get('sl_pct_min')}"
        
        if orig_sl_max != updated_config.get('sl_pct_max'):
            change['changes']['sl_pct_max'] = f"{orig_sl_max} -> {updated_config.get('sl_pct_max')}"
        
        if change['changes']:
            changes.append(change)
    
    # Update mode section
    if 'mode' not in config:
        config['mode'] = {}
    
    old_profile = config['mode'].get('risk_profile', 'normal')
    config['mode']['risk_profile'] = overall_profile
    config['mode']['last_update'] = datetime.now(timezone.utc).isoformat()
    config['mode']['reason'] = f"auto-adjustment based on market volatility"
    
    if old_profile != overall_profile:
        changes.append({
            'symbol': 'GLOBAL',
            'changes': {
                'risk_profile': f"{old_profile} -> {overall_profile}"
            }
        })
    
    # Save updated config
    if save_risk_config(config):
        logger.info("\n" + "=" * 60)
        logger.info("Risk configuration updated successfully!")
        logger.info("=" * 60)
        
        if changes:
            logger.info("\nChanges made:")
            for change in changes:
                logger.info(f"\n  {change['symbol']}:")
                for key, val in change.get('changes', {}).items():
                    logger.info(f"    {key}: {val}")
        else:
            logger.info("\nNo parameter changes needed (market conditions stable)")
        
        logger.info(f"\nNew risk profile: {overall_profile}")
        return True
    else:
        logger.error("Failed to save risk configuration")
        return False


def main():
    """Main entry point for the scheduler."""
    logger.info("Dynamic Risk Adjustment Scheduler started")
    
    try:
        # Run the async adjustment
        import asyncio
        result = asyncio.run(run_risk_adjustment())
        
        if result:
            logger.info("Risk adjustment completed successfully")
            sys.exit(0)
        else:
            logger.error("Risk adjustment failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Scheduler interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
