#!/bin/zsh

# 1) Matar cualquier proceso de OpenClaw que quede colgado
pkill -9 -f openclaw || true

# 2) Limpiar locks si existen (sin error si no hay)
rm -f /tmp/openclaw/*.lock 2>/dev/null || true
rm -f ~/.openclaw/*.lock 2>/dev/null || true

# 3) Arrancar gateway en puerto 18789, verbose y permitiendo config actual
cd /Users/FIRMAS/.openclaw
openclaw gateway --port 18789 --verbose --allow-unconfigured
