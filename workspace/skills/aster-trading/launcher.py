#!/usr/bin/env python3
"""
Sistema de Trading V2 Simplificado
===================================

Arquitectura:
- Cron de OpenClaw cada 5 min = health check
- Cron de OpenClaw cada 60 min = reporte completo (vía OpenClaw native)
- Sistema V2 en background = ejecuta trades automáticamente
- Notificaciones = OpenClaw native capabilities
"""

import os
import sys
import json
import time
import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# Configuración
BASE_DIR = Path("/Users/FIRMAS/.openclaw")
SKILLS_DIR = BASE_DIR / "workspace" / "skills"
V2_DIR = SKILLS_DIR / "aster-trading"
VENV_PYTHON = BASE_DIR / ".venv/bin/python"

# Logging
LOG_FILE = V2_DIR / "logs" / "v2_system.log"
LOG_FILE.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class SimpleLauncher:
    """
    Lanzador simple para el sistema V2
    """
    
    PID_FILE = V2_DIR / "v2_process.pid"
    STATE_FILE = V2_DIR / "logs" / "v2_state.json"
    
    def __init__(self):
        self.v2_script = V2_DIR / "src" / "trading_system.py"
    
    def is_running(self) -> bool:
        """Verifica si el sistema está corriendo"""
        if not self.PID_FILE.exists():
            return False
        
        try:
            with open(self.PID_FILE, "r") as f:
                pid = int(f.read().strip())
            
            # Verificar que el proceso existe
            os.kill(pid, 0)  # Signal 0 = solo verificar
            return True
        except:
            return False
    
    def start(self):
        """Inicia el sistema V2"""
        if self.is_running():
            logger.info("Sistema V2 ya está corriendo")
            return False
        
        logger.info("🚀 Iniciando sistema V2...")
        
        try:
            # Iniciar proceso en background
            subprocess.Popen(
                [str(VENV_PYTHON), str(self.v2_script)],
                stdout=open(LOG_FILE, "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            
            # Guardar PID
            time.sleep(1)
            
            # Buscar PID real
            result = subprocess.run(
                ["pgrep", "-f", "trading_system.py"],
                capture_output=True,
                text=True
            )
            
            if result.stdout:
                pid = result.stdout.strip().split()[0]
                with open(self.PID_FILE, "w") as f:
                    f.write(pid)
                logger.info(f"✅ Sistema V2 iniciado (PID: {pid})")
            else:
                logger.warning("⚠️ Proceso iniciado pero PID no encontrado")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error al iniciar: {e}")
            return False
    
    def stop(self):
        """Detiene el sistema V2"""
        if not self.is_running():
            logger.info("Sistema V2 no está corriendo")
            return True
        
        logger.info("🛑 Deteniendo sistema V2...")
        
        try:
            with open(self.PID_FILE, "r") as f:
                pid = int(f.read().strip())
            
            os.kill(pid, 9)  # SIGKILL
            self.PID_FILE.unlink()
            
            logger.info("✅ Sistema V2 detenido")
            return True
            
        except Exception as e:
            logger.error(f"Error al detener: {e}")
            # Forzar limpieza
            if self.PID_FILE.exists():
                self.PID_FILE.unlink()
            return False
    
    def restart(self):
        """Reinicia el sistema"""
        self.stop()
        time.sleep(2)
        self.start()
    
    def status(self) -> dict:
        """Obtiene estado del sistema"""
        running = self.is_running()
        
        state = {
            "running": running,
            "timestamp": datetime.now().isoformat()
        }
        
        if running and self.STATE_FILE.exists():
            try:
                with open(self.STATE_FILE, "r") as f:
                    state.update(json.load(f))
            except:
                pass
        
        return state


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Launcher Aster Trading V2")
    parser.add_argument("action", choices=["start", "stop", "restart", "status"],
                       help="Acción a realizar")
    
    args = parser.parse_args()
    
    launcher = SimpleLauncher()
    
    if args.action == "start":
        launcher.start()
    elif args.action == "stop":
        launcher.stop()
    elif args.action == "restart":
        launcher.restart()
    elif args.action == "status":
        state = launcher.status()
        print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
