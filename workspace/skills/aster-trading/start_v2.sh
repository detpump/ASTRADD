#!/bin/bash
# =============================================================================
# Script de inicio para Trading System V2
# =============================================================================
# Este script inicia el sistema V2 en background y configura el cron de OpenClaw
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="/Users/FIRMAS/.openclaw/.venv/bin/python"

echo "=============================================="
echo "  ASTER TRADING V2 - STARTUP SCRIPT"
echo "=============================================="

# Verificar que existe el venv
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Error: Virtual environment no encontrado"
    exit 1
fi

# Función para iniciar el sistema
start_v2() {
    echo "🚀 Iniciando Trading System V2..."
    
    # CRITICAL: Must run from .openclaw root so .env is found
    # The .env file is at /Users/FIRMAS/.openclaw/.env
    cd /Users/FIRMAS/.openclaw
    
    # Iniciar en background desde el directorio correcto
    nohup $VENV_PYTHON $SCRIPT_DIR/src/trading_system.py > $SCRIPT_DIR/logs/v2_output.log 2>&1 &
    
    echo "✅ Sistema V2 iniciado (PID: $!)"
}

# Función para detener el sistema
stop_v2() {
    echo "🛑 Deteniendo Trading System V2..."
    
    # Buscar proceso de trading_system.py
    PID=$(ps aux | grep "trading_system.py" | grep -v grep | awk '{print $2}')
    
    if [ -n "$PID" ]; then
        kill $PID
        echo "✅ Sistema V2 detenido (PID: $PID)"
    else
        echo "⚠️ Sistema V2 no estaba corriendo"
    fi
}

# Función para ver logs
show_logs() {
    tail -f $SCRIPT_DIR/../logs/v2_output.log
}

# Función para estado
status_v2() {
    if pgrep -f "trading_system.py" > /dev/null; then
        echo "🟢 Sistema V2 está CORRIENDO"
    else
        echo "🔴 Sistema V2 está DETENIDO"
    fi
}

# Función para integrar con cron de OpenClaw
setup_cron() {
    echo "⚙️ Configurando cron de OpenClaw..."
    
    # CRITICAL: Must run from .openclaw root so .env is found
    # Crear job de cron - run from .openclaw root
    CRON_JOB="*/5 * * * * cd /Users/FIRMAS/.openclaw && $VENV_PYTHON $SCRIPT_DIR/cron_integration.py >> /Users/FIRMAS/.openclaw/logs/cron_v2.log 2>&1"
    
    # Añadir al crontab (primero quitar si existe)
    crontab -l 2>/dev/null | grep -v "cron_integration.py" | crontab -
    echo "$CRON_JOB" | crontab -
    
    echo "✅ Cron configurado (cada 5 minutos)"
    echo "Jobs de cron actuales:"
    crontab -l
}

# Función para iniciar todo
start_all() {
    start_v2
    
    # Esperar un poco
    sleep 2
    
    # Mostrar estado
    status_v2
    
    echo ""
    echo "📋 Para ver logs: $0 logs"
    echo "📋 Para detener: $0 stop"
    echo "📋 Para estado: $0 status"
}

# =============================================================================
# MENÚ PRINCIPAL
# =============================================================================

case "${1:-start}" in
    start)
        start_all
        ;;
    stop)
        stop_v2
        ;;
    restart)
        stop_v2
        sleep 2
        start_v2
        ;;
    status)
        status_v2
        ;;
    logs)
        show_logs
        ;;
    cron)
        setup_cron
        ;;
    *)
        echo "Uso: $0 {start|stop|restart|status|logs|cron}"
        echo ""
        echo "Comandos:"
        echo "  start   - Inicia el sistema V2"
        echo "  stop    - Detiene el sistema V2"
        echo "  restart - Reinicia el sistema V2"
        echo "  status  - Muestra el estado"
        echo "  logs    - Muestra los logs en tiempo real"
        echo "  cron    - Configura el cron de OpenClaw para supervision"
        exit 1
        ;;
esac
