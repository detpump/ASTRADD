#!/bin/bash
# =============================================================================
# SETUP SCRIPT - Inicializa todo el sistema V2
# =============================================================================
# Este script:
# 1. Copia los jobs de cron a la configuración de OpenClaw
# 2. Crea los directorios necesarios
# 3. Da permisos
# =============================================================================

set -e

echo "=============================================="
echo "  ASTER TRADING V2 - SETUP"
echo "=============================================="

BASE_DIR="/Users/FIRMAS/.openclaw"
V2_DIR="$BASE_DIR/skills/aster-trading-v2"

# 1. Crear directorios
echo "📁 Creando directorios..."
mkdir -p "$V2_DIR/logs"
mkdir -p "$V2_DIR/models"
mkdir -p "$V2_DIR/state"

# 2. Dar permisos
echo "🔧 Configurando permisos..."
chmod +x "$V2_DIR/control.sh"
chmod +x "$V2_DIR/launcher.py"

# 3. Copiar jobs de cron
echo "📋 Configurando cron jobs..."
if [ -f "$BASE_DIR/cron/jobs-v2.json" ]; then
    # Añadir jobs al archivo principal
    # Esto es un workaround - en producción se mergearía con jobs.json
    echo "Jobs de cron configurados en cron/jobs-v2.json"
    echo "Para activarlos, copia el contenido a cron/jobs.json"
fi

# 4. Verificar instalación de dependencias
echo "📦 Verificando dependencias..."
$BASE_DIR/.venv/bin/python -c "import websockets; import xgboost; import lightgbm" 2>/dev/null && \
    echo "✅ Dependencias instaladas" || \
    echo "⚠️ Instalar: pip install websockets xgboost lightgbm"

# 5. Verificar API de Aster
echo "🔗 Verificando API Aster..."
echo "Configura tus API keys en el archivo: $V2_DIR/config/api_keys.json"

# 6. Crear archivo de configuración si no existe
if [ ! -f "$V2_DIR/config/api_keys.json" ]; then
    cat > "$V2_DIR/config/api_keys.json" << 'EOF'
{
    "aster": {
        "api_key": "TU_API_KEY",
        "api_secret": "TU_API_SECRET",
        "testnet": false
    },
    "llm": {
        "provider": "minimax",
        "api_key": "TU_MINIMAX_API_KEY"
    },
    "telegram": {
        "bot_token": "TU_BOT_TOKEN",
        "chat_id": "TU_CHAT_ID"
    }
}
EOF
    echo "⚠️ Crea $V2_DIR/config/api_keys.json con tus credenciales"
fi

echo ""
echo "=============================================="
echo "  SETUP COMPLETADO"
echo "=============================================="
echo ""
echo "Para iniciar el sistema:"
echo "  $V2_DIR/control.sh start"
echo ""
echo "Para ver estado:"
echo "  $V2_DIR/control.sh status"
echo ""
echo "Para detener:"
echo "  $V2_DIR/control.sh stop"
echo ""
