#!/usr/bin/env python3
"""
Cron Job: Reporte Horario
=========================

This module generates hourly reports for the trading system.
Reports are written to OpenClaw's centralized logging system.

Note: Telegram integration removed - notifications are handled by 
OpenClaw's native capabilities through the centralized_logger.

Author: Aster Trading V2
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from centralized_logger import CentralizedLogger
from state.state_service import state_service
from state.models import RiskState, Position, ExecutionTracker, LastSignal, MarketPrice

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OpenClawReporter:
    """
    Reporter using OpenClaw's native centralized logging.
    """
    
    def __init__(self):
        self.logger = CentralizedLogger()
        self.state = {}
    
    def _load_state(self) -> Dict:
        """Load current system state from DB via state_service."""
        try:
            positions = {p.symbol: p for p in state_service.get_positions()}
            tracker = state_service.get_execution_tracker()

            # TODO: when market_prices/last_signals tables are live, swap to direct reads
            market_prices = {}
            signals = {}

            return {
                "running": True,  # cron assumes scheduler is active; absence handled downstream
                "loop_count": tracker.total_signals if tracker else 0,
                "trades_executed": tracker.total_orders if tracker else 0,
                "open_positions": len(positions),
                "positions": {k: v.model_dump() for k, v in positions.items()},
                "market": market_prices,
                "last_signals": signals,
            }
        except Exception as e:
            logger.warning(f"Could not load state from DB: {e}")
        return {}
    
    def _load_risk_state(self) -> Dict:
        """Load current risk state from DB via state_service."""
        try:
            rs = state_service.get_risk_state()
            if rs:
                return rs.model_dump()
        except Exception as e:
            logger.warning(f"Could not load risk state from DB: {e}")
        return {}
    
    def generate_report(self) -> str:
        """Generate a comprehensive status report"""
        state = self._load_state()
        risk = self._load_risk_state()
        
        # Build report
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        report_lines = [
            "=" * 50,
            "ASTER TRADING V2 - HOURLY REPORT",
            f"Time: {timestamp}",
            "=" * 50,
            "",
            "SYSTEM STATUS:",
        ]
        
        # System running status
        running = state.get("running", False)
        report_lines.append(f"  Running: {'YES' if running else 'NO'}")
        
        if running:
            uptime = state.get("uptime_seconds", 0)
            report_lines.append(f"  Uptime: {uptime/3600:.1f} hours")
            report_lines.append(f"  Loop Count: {state.get('loop_count', 0)}")
            report_lines.append(f"  Trades Executed: {state.get('trades_executed', 0)}")
        
        # Equity
        report_lines.extend([
            "",
            "EQUITY:",
        ])
        equity = risk.get("account_equity", state.get("equity", 0))
        report_lines.append(f"  Current: ${equity:.2f}")
        
        daily_pnl = risk.get("daily_pnl", 0)
        daily_pnl_pct = risk.get("daily_pnl_pct", 0)
        report_lines.append(f"  Daily P&L: ${daily_pnl:.2f} ({daily_pnl_pct*100:+.2f}%)")
        
        drawdown = risk.get("drawdown_pct", 0)
        report_lines.append(f"  Drawdown: {drawdown*100:.2f}%")
        
        # Positions
        report_lines.extend([
            "",
            "POSITIONS:",
        ])
        open_positions_count = risk.get("open_positions_count", len(state.get("positions", {})))
        report_lines.append(f"  Open: {open_positions_count}")
        
        # Market prices
        market = state.get("market", {})
        if market:
            report_lines.extend([
                "",
                "MARKET PRICES:",
            ])
            for symbol, price in market.items():
                report_lines.append(f"  {symbol}: ${price:.6f}")
        
        # Signals
        signals = state.get("last_signals", {})
        if signals:
            report_lines.extend([
                "",
                "CURRENT SIGNALS:",
            ])
            for symbol, sig in signals.items():
                action = sig.get("action", "HOLD")
                strength = sig.get("strength", sig.get("signal_strength", 0))
                report_lines.append(f"  {symbol}: {action} (strength: {strength:.2f})")
        
        # Risk status
        report_lines.extend([
            "",
            "RISK STATUS:",
        ])
        risk_level = risk.get("risk_level", "UNKNOWN")
        report_lines.append(f"  Level: {risk_level}")
        
        trades_today = risk.get("trades_today", 0)
        report_lines.append(f"  Trades Today: {trades_today}")
        
        report_lines.extend([
            "",
            "=" * 50,
        ])
        
        return "\n".join(report_lines)
    
    def log_report(self) -> bool:
        """
        Log the hourly report to OpenClaw centralized logger.
        
        Returns:
            True if successful
        """
        try:
            state = self._load_state()
            risk = self._load_risk_state()

            # Log heartbeat with current state
            self.logger.log_heartbeat(
                running=state.get("running", False),
                equity=risk.get("account_equity", 0),
                positions=state.get("positions", {}),
                signals=state.get("last_signals", {}),
                market=state.get("market", {}),
                loop_count=state.get("loop_count", 0),
                trades_executed=state.get("trades_executed", 0),
                risk_level=risk.get("risk_level", "UNKNOWN"),
            )

            # Log equity
            self.logger.log_equity(
                equity=risk.get("account_equity", 0),
                daily_pnl=risk.get("daily_pnl", 0),
                drawdown_pct=risk.get("drawdown_pct", 0),
            )

            # Log risk status
            self.logger.log_risk(
                risk_level=risk.get("risk_level", "UNKNOWN"),
                equity=risk.get("account_equity", 0),
                max_position_pct=risk.get("max_position_pct", 0),
                current_exposure_pct=risk.get("exposure_pct", 0),
                daily_loss_pct=risk.get("daily_pnl_pct", 0),
                drawdown_pct=risk.get("drawdown_pct", 0),
                open_positions_count=risk.get("open_positions_count", 0),
                trades_today=risk.get("trades_today", 0),
                can_trade=risk.get("can_trade", True),
            )

            logger.info("Hourly report logged to OpenClaw")
            return True

        except Exception as e:
            logger.error(f"Error logging report: {e}")
            return False
    
    def run(self):
        """Run the hourly report"""
        logger.info("Generating hourly report...")
        
        # Generate and print report
        report = self.generate_report()
        print(report)
        
        # Log to OpenClaw
        self.log_report()
        
        return report


def main():
    """Main entry point"""
    reporter = OpenClawReporter()
    reporter.run()


if __name__ == "__main__":
    main()
