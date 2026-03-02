# HEARTBEAT.md

Cuando recibas un heartbeat de trading, debes:

1. Lee y sigue las instrucciones de `/Users/FIRMAS/.openclaw/workspace/skills/aster-trading/data/SKILL.md`.
2. **NO ejecutes trading manualmente** - el sistema V2 corre en background.
3. **Verifica el estado**: Lee `logs/v2_state.json` para ver si el sistema está corriendo.
4. Si el sistema no está corriendo, **inícialo**: `./control.sh start`
5. Responde solo con las 6 líneas de resumen definidas en el SKILL.

Esto aplica especialmente cuando el mensaje contenga 'HB trading 5m' o similar.
