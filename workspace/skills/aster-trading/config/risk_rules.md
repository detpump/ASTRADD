# Reglas de Riesgo — NO NEGOCIABLES

Estas reglas definen el marco duro de riesgo.  
Los valores numéricos concretos se toman SIEMPRE de `risk_config.json`.  
Si `risk_config.json` y estas reglas entran en conflicto, prevalecen estas reglas.

---

## 1. Símbolos soportados

- ETHUSDT
- ASTERUSDT
- BNBUSDT
- SOLUSDT
- HYPEUSDT

El bot **solo** debe operar en estos símbolos.

---

## 2. Equity y tamaño por operación

- El tamaño de cada operación debe respetar:
  - El porcentaje máximo de equity por trade definido en  
    `risk_config.json.global.max_equity_risk_pct_per_trade`.
  - El notional mínimo y máximo por símbolo definidos en  
    `risk_config.json.symbols[SYM].min_notional_usdt` y `max_notional_usdt`.

- La suma de los notionals de todas las posiciones abiertas no debe superar
  `risk_config.json.global.max_equity_notional_pct` del equity total.

Si el cálculo de tamaño viola estos límites, el agente debe hacer **HOLD** y no abrir nuevas posiciones en ese ciclo.

---

## 3. Leverage permitido

- El leverage efectivo por símbolo nunca debe exceder:
  `risk_config.json.symbols[SYM].max_leverage`.

- El agente **no** debe intentar fijar un leverage superior a ese valor.
- El código que llama al endpoint de Aster (`/fapi/v1|v3/leverage`) debe
  recortar cualquier petición de leverage a ese máximo.

Si por cualquier motivo el leverage actual en el exchange supera este máximo, el agente debe:
- evitar añadir tamaño,
- y, si procede, reducir la posición hasta que el notional y el leverage queden dentro de límites.

---

## 4. Número de posiciones

- Máximo **una** posición abierta por símbolo a la vez.
- No abrir una nueva posición en un símbolo si:
  - ya hay posición abierta en ese símbolo,
  - o `trade_state.json` refleja un estado inconsistente.

---

## 5. Pérdida máxima

- Pérdida máxima por operación:
  - el SL y el tamaño de la posición deben estar configurados de forma que la pérdida máxima **estimada** por trade no supere el porcentaje de equity implícito en
    `risk_config.json.global.max_equity_risk_pct_per_trade`.

- Pérdida diaria máxima:
  - Si la pérdida diaria acumulada supera
    `risk_config.json.global.daily_loss_hard_limit_usdt` (en negativo),
    el agente solo puede:
    - mantener (HOLD) posiciones existentes,
    - o cerrarlas (CLOSE),
    - y **no** debe abrir nuevas en lo que queda de día.

- Si el PnL no realizado de una posición se aproxima a una pérdida excesiva
  según estos límites, el agente debe favorecer cerrar o reducir la posición
  en lugar de añadir tamaño.

---

## 6. Gestión de beneficios

- El uso de TP escalonados y trailing stop debe estar alineado con:
  - los niveles de SL/TP que calcule el código,
  - y los límites de riesgo de `risk_config.json`.

- El agente puede:
  - reducir parte de la posición cuando el PnL sea claramente positivo,
  - mover el SL a break-even y luego a beneficio,
  - siempre que dichas acciones no incrementen el riesgo más allá de los límites definidos.

---

## 7. Reglas generales de ejecución

- Siempre llamar a `sync_state_from_exchange.py` antes de gestionar SL/TP.
- Siempre fijar leverage dentro de los rangos permitidos por `risk_config.json` ANTES de abrir una posición.
- Si `get_market_state.py` falla o devuelve error:
  - el agente debe responder **HOLD, no operar** en ese ciclo.
- Si cualquier script de trading falla (errores de red, API, etc.):
  - no reintentar en el mismo ciclo,
  - reportar el error en la línea 4 del resumen,
  - y no ejecutar nuevas acciones de riesgo en ese turno.

---

## 8. Ventanas horarias donde NO abrir nuevas posiciones


- No abras nuevas posiciones entre las 00:00 y las 06:00 (hora local) salvo que ya haya una posición abierta a gestionar (SL/TP/surf sí se gestionan).
- No abras nuevas posiciones entre las 14:25 y las 14:45 (hora local) de lunes a viernes para evitar picos de volatilidad por noticias macro USA.
- Fuera de estas ventanas, puedes abrir nuevas posiciones siempre que el resto de reglas de riesgo se cumplan.
