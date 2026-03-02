#!/usr/bin/env python3
"""
Sistema Híbrido OpenClaw + Trading V2
=====================================

Este script explica cómo integrar el nuevo sistema ML con el cron de OpenClaw.

ARQUITECTURA HÍBRIDA:
---------------------

┌─────────────────────────────────────────────────────────────────────────┐
│                        OPENCLAW CRON (cada 5 min)                      │
├─────────────────────────────────────────────────────────────────────────┤
│  • Health checks                                                       │
│  • Reporting (OpenClaw native)                                       │
│  • Estado del sistema                                                  │
│  • Risk guards                                                         │
│  • Revisión de posiciones                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    TRADING V2 (Background Service)                     │
├─────────────────────────────────────────────────────────────────────────┤
│  • WebSocket connection 24/7                                          │
│  • ML signal generation (XGBoost)                                     │
│  • LLM Guardian (limitado - 1x/hora)                                  │
│  • Trade execution                                                     │
│  • Real-time market data                                              │
└─────────────────────────────────────────────────────────────────────────┘

El cron de OpenClaw vigila el sistema V2 y reporta, pero no ejecuta trades.
El sistema V2 corre en background y hace todo el trabajo pesado.

========================================================================
"""

import os
import sys
import time
import asyncio
import logging
from datetime import datetime

# Agregar paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =======================
# CRON JOBS (OpenClaw)
# =======================

class OpenClawCron:
    """
    Jobs de cron de OpenClaw para supervisar el sistema V2
    """
    
    def __init__(self):
        # Use centralized paths - single point of change for server migration
        # DB is now the source of truth; state_file kept only for legacy fallback
        self.state_file = None
        self.last_report_time = 0
        self.report_interval = 300  # 5 minutos
        
    def check_v2_system(self) -> dict:
        """
        Verifica si el sistema V2 está corriendo
        Lee el estado desde DB
        """
        try:
            from state.state_service import state_service

            system_state = state_service.get_system_state()
            if not system_state:
                return {
                    "running": False,
                    "reason": "System state not found in DB"
                }

            tracker = state_service.get_execution_tracker()
            now_ms = int(time.time() * 1000)

            return {
                "running": bool(system_state.running),
                "reason": "OK" if system_state.running else "System marked as stopped",
                "last_update": now_ms,
                "uptime_seconds": max(0, (now_ms - (system_state.start_time or now_ms)) // 1000),
                "loop_count": system_state.loop_count or 0,
                "trades_executed": system_state.trades_executed or 0,
                "errors": system_state.recent_errors or [],
                "components": {
                    "execution_tracker": {
                        "total_signals": tracker.total_signals if tracker else 0,
                        "total_orders": tracker.total_orders if tracker else 0,
                        "active_orders": tracker.active_orders if tracker else 0,
                    }
                }
            }
        except Exception as e:
            return {
                "running": False,
                "reason": f"Error reading DB state: {e}"
            }
    
    def check_risk_status(self) -> dict:
        """Verifica el estado de riesgo desde DB (RiskState)."""
        try:
            from state.state_service import state_service

            rs = state_service.get_risk_state()
            if not rs:
                return {"error": "Risk state not found"}

            risk = rs.model_dump()
            return {
                "equity": risk.get("account_equity", 0),
                "daily_pnl": risk.get("daily_pnl", 0),
                "drawdown_pct": risk.get("drawdown_pct", 0),
                "open_positions": risk.get("open_positions_count", 0),
                "risk_level": self._calculate_risk_level(risk),
            }

        except Exception as e:
            return {"error": str(e)}
    
    def _calculate_risk_level(self, risk: dict) -> str:
        """Calcula nivel de riesgo"""
        dd = risk.get("drawdown_pct", 0)
        daily_loss = risk.get("daily_pnl", 0)
        
        if dd > 0.15 or daily_loss < -risk.get("equity", 1) * 0.05:
            return "CRITICAL"
        elif dd > 0.10:
            return "HIGH"
        elif dd > 0.05:
            return "NORMAL"
        return "LOW"
    
    def generate_report(self) -> str:
        """
        Genera reporte para OpenClaw native notifications
        """
        # Verificar sistema
        v2_status = self.check_v2_system()
        risk_status = self.check_risk_status()
        
        report = f"""
📊 **ASTER TRADING V2 - REPORTE**

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🟢 **Sistema V2:**
"""
        
        if v2_status.get("running"):
            report += f"   • Estado: ✅ CORRIENDO\n"
            report += f"   • Uptime: {v2_status.get('uptime_seconds', 0)/3600:.1f}h\n"
            report += f"   • Loops: {v2_status.get('loop_count', 0)}\n"
            report += f"   • Trades: {v2_status.get('trades_executed', 0)}\n"
            
            # Componentes
            comps = v2_status.get("components", {})
            ml = comps.get("ml_model", {})
            report += f"   • ML: {ml.get('model_type', 'N/A')}\n"
            
            llm = comps.get("llm", {})
            report += f"   • LLM calls today: {llm.get('calls_today', 0)}\n"
        else:
            report += f"   • Estado: ❌ {v2_status.get('reason')}\n"
        
        report += f"""
⚠️ **Riesgo:**
   • Equity: ${risk_status.get('equity', 0):.2f}
   • Daily P&L: ${risk_status.get('daily_pnl', 0):.2f}
   • Drawdown: {risk_status.get('drawdown_pct', 0)*100:.1f}%
   • Posiciones: {risk_status.get('open_positions', 0)}
   • Nivel: {risk_status.get('risk_level', 'N/A')}
"""
        
        # Errors
        errors = v2_status.get("errors", [])
        if errors:
            report += f"\n❗ **Últimos errores:**\n"
            for err in errors[-3:]:
                report += f"   • {err}\n"
        
        return report
    
    def run_cron_job(self):
        """
        Ejecuta un job de cron
        """
        print("=" * 60)
        print("CRON JOB - OpenClaw Supervision")
        print("=" * 60)
        
        # Generar reporte
        report = self.generate_report()
        print(report)
        
        # Aquí se enviaría a través de OpenClaw native notifications
        # log_to_centralized_logger(report)
        
        return report


# =======================
# MAIN (para testing)
# =======================

def main():
    """Main para testing"""
    cron = OpenClawCron()
    cron.run_cron_job()


if __name__ == "__main__":
    main()
