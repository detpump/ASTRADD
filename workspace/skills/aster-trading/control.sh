#!/bin/bash
# =============================================================================
# ASTER TRADING V2 - CONTROL SIMPLE
# =============================================================================
# Uso fácil: ./control.sh start | stop | status | log
# =============================================================================

V2_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$V2_DIR/logs/system_init.log"
PYTHON="/Users/FIRMAS/.openclaw/.venv/bin/python"
PID_FILE="$V2_DIR/v2_process.pid"
LISTENER_PID_FILE="$V2_DIR/account_listener.pid"
BRACKETS_PID_FILE="$V2_DIR/manage_brackets.pid"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'


append_system_log() {
    local subsystem="$1"
    local action="$2"
    local status="$3"
    local message="${4:-}"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local sanitized=${message//\\/\\\\}
    sanitized=${sanitized//\"/\\\"}
    sanitized=${sanitized//$'\n'/ }
    sanitized=${sanitized//$'\r'/ }
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "{\"timestamp\":\"$ts\",\"subsystem\":\"$subsystem\",\"action\":\"$action\",\"status\":\"$status\",\"message\":\"$sanitized\"}" >> "$LOG_FILE"
}

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $1"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)]${NC} $1"; }
error() { echo -e "${RED}[$(date +%H:%M:%S)]${NC} $1"; }

case "$1" in
    start)
        log "🚀 Iniciando Trading V2..."

        # Launch account listener first (idempotent)
        if [ -f "$LISTENER_PID_FILE" ]; then
            LPID=$(cat "$LISTENER_PID_FILE")
            if kill -0 "$LPID" 2>/dev/null; then
                warn "Account stream listener ya estaba corriendo"
            else
                rm -f "$LISTENER_PID_FILE"
            fi
        fi

        if [ ! -f "$LISTENER_PID_FILE" ]; then
            cd "$V2_DIR"
            nohup PYTHONPATH=src $PYTHON -m services.account_stream_listener \
                > "$V2_DIR/logs/account_stream.log" 2>&1 &
            sleep 2
            LPID=$(pgrep -f "services.account_stream_listener" | head -1)
            if [ -n "$LPID" ]; then
                echo "$LPID" > "$LISTENER_PID_FILE"
                log "Account stream listener started (PID: $LPID)"
            else
                warn "WARNING: Could not start account stream listener"
            fi
        fi

        # Verificar si ya corre
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                warn "Ya está corriendo (PID: $PID)"
                exit 0
            fi
            rm -f "$PID_FILE"
        fi
        
        # Iniciar
        cd "$V2_DIR/src"
        nohup $PYTHON trading_system.py > "$V2_DIR/logs/v2_output.log" 2>&1 &
        
        # Esperar y obtener PID
        sleep 2
        PID=$(pgrep -f "trading_system.py" | head -1)
        
        if [ -n "$PID" ]; then
            echo "$PID" > "$PID_FILE"
            log "✅ Trading V2 iniciado (PID: $PID)"
        else
            error "❌ Error al iniciar"
            exit 1
        fi
        ;;
        
    stop)
        log "🛑 Deteniendo Trading V2..."
        
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            kill "$PID" 2>/dev/null
            rm -f "$PID_FILE"
            log "✅ Detenido"
        else
            # Buscar y matar
            PIDS=$(pgrep -f "trading_system.py")
            if [ -n "$PIDS" ]; then
                kill $PIDS 2>/dev/null
                warn "Proceso matado"
            else
                warn "No estaba corriendo"
            fi
        fi
        ;;
        
    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                log "🟢 CORRIENDO (PID: $PID)"
                
                # Mostrar estado rápido
                if [ -f "$V2_DIR/logs/v2_state.json" ]; then
                    echo ""
                    cat "$V2_DIR/logs/v2_state.json" | python -m json.tool 2>/dev/null | head -20
                fi
            else
                warn "🔴 NO CORRIENDO (PID file stale)"
                rm -f "$PID_FILE"
            fi
        else
            # Buscar si está corriendo sin PID file
            PIDS=$(pgrep -f "trading_system.py")
            if [ -n "$PIDS" ]; then
                log "🟡 CORRIENDO sin PID file (PIDs: $PIDS)"
            else
                warn "🔴 DETENIDO"
            fi
        fi
        ;;
        
    log)
        if [ -f "$V2_DIR/logs/v2_output.log" ]; then
            tail -50 "$V2_DIR/logs/v2_output.log"
        else
            error "No hay logs"
        fi
        ;;
        
    logf)
        # Follow logs
        if [ -f "$V2_DIR/logs/v2_output.log" ]; then
            tail -f "$V2_DIR/logs/v2_output.log"
        else
            error "No hay logs"
        fi
        ;;
        
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
        
    boot)
        # Instala/unistala servicio para inicio automático (LaunchDaemon)
        PLIST="/Library/LaunchDaemons/com.aster.tradingv2.plist"
        SOURCE_PLIST="$V2_DIR/com.aster.tradingv2.plist"
        
        if [ "$2" = "on" ]; then
            log "🔄 Instalando servicio para inicio automático..."
            sudo cp "$SOURCE_PLIST" "$PLIST"
            sudo launchctl load "$PLIST"
            log "✅ Servicio instalado. Iniciará en próximo arranque."
        elif [ "$2" = "off" ]; then
            log "🔄 Desinstalando servicio de inicio automático..."
            sudo launchctl unload "$PLIST" 2>/dev/null
            sudo rm -f "$PLIST"
            log "✅ Servicio desinstalado."
        else
            echo "Uso: $0 boot on|off"
            echo "  on  - Instalar para iniciar con el sistema"
            echo "  off - Quitar de inicio automático"
        fi
        ;;
        
    *)
        echo "Uso: $0 {start|stop|status|log|logf|restart|boot}"
        echo ""
        echo "Comandos:"
        echo "  start     - Iniciar sistema"
        echo "  stop      - Detener sistema"
        echo "  status    - Ver estado"
        echo "  log       - Ver últimos logs"
        echo "  logf      - Ver logs en tiempo real"
        echo "  restart   - Reiniciar"
        echo "  boot on   - Instalar para inicio automático"
        echo "  boot off  - Quitar de inicio automático"
        echo "  telegram  - Notificación (usar OpenClaw nativo)"
        exit 1
        ;;
esac
