#!/usr/bin/env python3
"""Long-lived Binance Futures (Aster) user-data listener.

Maintains a listenKey, consumes account/order websocket events, and keeps
``trade_state.json`` synchronized so higher-level services (manage_brackets,
recovery daemons, etc.) can react to TP/SL executions without REST polling.

Usage:
    from services.account_stream_listener import start_account_listener

    # Start once during system bootstrap – idempotent.
    start_account_listener()

    # Optionally query status
    from services.account_stream_listener import is_account_listener_active
    if is_account_listener_active():
        ...

The module also exposes ``stop_account_listener`` for graceful shutdowns when
the supervisor is exiting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import suppress
from typing import Any, Dict, Optional

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover - dependency missing during some tests
    websockets = None
    ConnectionClosed = Exception  # type: ignore

from api.aster_api import signed_post
from trade_state import (
    clear_position_state,
    get_position_state,
    init_position,
    record_partial_close,
    set_position_state,
)
from state.state_service import state_service
from state.models import Position, Order


logger = logging.getLogger(__name__)

LISTEN_KEY_ENDPOINT = "/fapi/v1/listenKey"
WS_URL_TEMPLATE = "wss://fstream.asterdex.com/stream?streams={listen_key}"
LISTEN_KEY_TTL_SECONDS = 30 * 60  # 30 minutes per requirements
LISTEN_KEY_KEEPALIVE_SECONDS = 25 * 60  # refresh a bit earlier for safety
RECONNECT_MAX_DELAY = 60


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AccountStreamListener:
    """Handle listenKey lifecycle and websocket streaming on a background thread."""

    def __init__(self) -> None:
        self._listen_key: Optional[str] = None
        self._listen_key_obtained: float = 0.0
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.RLock()
        self._last_event_ts: int = 0
        self._reconnect_attempts: int = 0
        self._ws: Optional[websockets.WebSocketClientProtocol] = None  # type: ignore

    # ------------------------------------------------------------------
    # Thread/loop lifecycle helpers
    # ------------------------------------------------------------------
    def start(self, force_restart: bool = False) -> bool:
        """Start the listener thread (idempotent)."""
        with self._lock:
            if websockets is None:
                logger.error("websockets package is not available; cannot start account listener")
                return False

            if self._thread and self._thread.is_alive():
                if not force_restart:
                    logger.debug("Account stream listener already running")
                    return True
                logger.info("Force restarting account stream listener")
                self.stop()

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._thread_entry,
                name="AccountStreamListener",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Request graceful shutdown and wait for the thread to exit."""
        with self._lock:
            if not self._thread:
                return

            self._stop_event.set()
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._request_shutdown(), self._loop)

            self._thread.join(timeout)
            if self._thread.is_alive():
                logger.warning("Account stream listener thread did not stop within %.1fs", timeout)
            self._thread = None
            self._loop = None
            self._running = False

    def _thread_entry(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception:  # pragma: no cover - critical to log
            logger.exception("Account stream listener crashed")
        finally:
            # Cancel dangling tasks to avoid warnings on shutdown
            pending = asyncio.all_tasks(loop=self._loop)
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(Exception):
                    self._loop.run_until_complete(task)
            self._loop.close()
            self._loop = None
            self._running = False

    async def _request_shutdown(self) -> None:
        if self._ws:
            with suppress(Exception):
                await self._ws.close()

    # ------------------------------------------------------------------
    # Main async loop
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        self._running = True
        logger.info("Account stream listener started")

        while not self._stop_event.is_set():
            if not await self._ensure_listen_key():
                await asyncio.sleep(5)
                continue

            ws_url = WS_URL_TEMPLATE.format(listen_key=self._listen_key)
            try:
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:  # type: ignore[arg-type]
                    self._ws = ws
                    self._reconnect_attempts = 0
                    logger.info("Connected to account stream (listenKey=%s)", self._listen_key)

                    keepalive_task = asyncio.create_task(self._listen_key_keepalive_loop())
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    except ConnectionClosed as exc:
                        if self._stop_event.is_set():
                            logger.info("Account stream websocket closed during shutdown: %s", exc)
                        else:
                            logger.warning("Account stream websocket closed: %s", exc)
                    except Exception as exc:  # pragma: no cover - network specific
                        logger.exception("Account stream listener error: %s", exc)
                    finally:
                        keepalive_task.cancel()
                        with suppress(Exception):
                            await keepalive_task
                        self._ws = None

                if self._stop_event.is_set():
                    break

            except Exception as exc:
                self._reconnect_attempts += 1
                delay = min((2 ** self._reconnect_attempts), RECONNECT_MAX_DELAY)
                logger.warning(
                    "Account stream connection failed (%s). Retrying in %.1fs (attempt %d)",
                    exc,
                    delay,
                    self._reconnect_attempts,
                )
                await asyncio.sleep(delay)

        logger.info("Account stream listener stopped")

    # ------------------------------------------------------------------
    # listenKey helpers
    # ------------------------------------------------------------------
    async def _ensure_listen_key(self) -> bool:
        if self._listen_key and (time.time() - self._listen_key_obtained) < (LISTEN_KEY_TTL_SECONDS - 60):
            return True
        return await asyncio.to_thread(self._create_listen_key)

    def _create_listen_key(self) -> bool:
        try:
            response = signed_post(LISTEN_KEY_ENDPOINT, {})
            listen_key = response.get("listenKey") or response.get("listen_key")
            if listen_key:
                self._listen_key = listen_key
                self._listen_key_obtained = time.time()
                logger.info("Obtained listenKey for account stream")
                return True
            logger.error("listenKey response missing key: %s", response)
        except Exception as exc:  # pragma: no cover - network specific
            logger.exception("Unable to obtain listenKey: %s", exc)
        return False

    async def _listen_key_keepalive_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(LISTEN_KEY_KEEPALIVE_SECONDS)
            await asyncio.to_thread(self._keepalive_listen_key)

    def _keepalive_listen_key(self) -> None:
        if not self._listen_key:
            return
        try:
            signed_post(LISTEN_KEY_ENDPOINT, {"listenKey": self._listen_key})
            self._listen_key_obtained = time.time()
            logger.debug("listenKey keepalive sent")
        except Exception as exc:  # pragma: no cover - network specific
            logger.warning("listenKey keepalive failed: %s", exc)

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------
    async def _handle_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Malformed account stream payload: %s", raw[:200])
            return

        payload = message.get("data", message)
        event_type = payload.get("e")
        self._last_event_ts = _safe_int(payload.get("E"), int(time.time() * 1000))

        if event_type == "ACCOUNT_UPDATE":
            self._handle_account_update(payload)
        elif event_type == "ORDER_TRADE_UPDATE":
            self._handle_order_trade_update(payload)
        elif event_type == "MARGIN_CALL":
            self._handle_margin_call(payload)
        else:
            logger.debug("Unhandled account stream event: %s", event_type)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _handle_account_update(self, payload: Dict[str, Any]) -> None:
        account = payload.get("a", {})
        positions = account.get("P", [])
        event_ts = _safe_int(payload.get("E"), int(time.time() * 1000))

        for position in positions:
            symbol = position.get("s")
            if not symbol:
                continue

            raw_amount = _safe_float(position.get("pa"))
            entry_price = _safe_float(position.get("ep"))
            unrealized = _safe_float(position.get("up"))

            if abs(raw_amount) < 1e-9:
                if get_position_state(symbol):
                    logger.info("Clearing position %s (size=0) from trade_state", symbol)
                    clear_position_state(symbol)
                # Remove from DB snapshot
                try:
                    state_service.delete_position(symbol)
                except Exception:
                    logger.exception("Failed to delete position %s from DB", symbol)
                continue

            side = "BUY" if raw_amount > 0 else "SELL"
            size = abs(raw_amount)

            state = get_position_state(symbol)
            if not state:
                if entry_price <= 0:
                    logger.debug("Skipping new position %s due to missing entry price", symbol)
                    continue
                logger.info("Creating trade_state entry from account update: %s", symbol)
                state = init_position(symbol, side, entry_price, size, ts=str(event_ts))
            else:
                state["side"] = side
                if entry_price > 0:
                    state["entry_price"] = entry_price
                state["size"] = size
                state.setdefault("open_time", event_ts)
                state["last_exchange_sync"] = event_ts
                state["unrealized_pnl"] = unrealized
                set_position_state(symbol, state)

            # Upsert DB snapshot
            try:
                position_row = Position(
                    symbol=symbol,
                    side=side,
                    quantity=size,
                    entry_price=entry_price,
                    mark_price=state.get("mark_price") or entry_price,
                    unrealized_pnl=unrealized,
                    notional=abs(size * entry_price),
                    leverage=int(state.get("leverage", 1) or 1),
                    open_time=state.get("open_time"),
                    cycle_id=state.get("cycle_id"),
                    metadata={"last_exchange_sync": state.get("last_exchange_sync")},
                )
                state_service.upsert_position(position_row)
            except Exception:
                logger.exception("Failed to upsert position %s into DB", symbol)

    def _handle_order_trade_update(self, payload: Dict[str, Any]) -> None:
        order = payload.get("o", {})
        symbol = order.get("s")
        if not symbol:
            return

        order_id = str(order.get("i", ""))
        order_status = order.get("X", "")
        order_type = str(order.get("ot", "")).upper()

        state = get_position_state(symbol)
        if not state:
            return

        bracket_label = self._detect_bracket_label(state, order_id)

        if order_status == "CANCELED" and bracket_label:
            key = f"{bracket_label.lower()}_order_id"
            state[key] = None
            set_position_state(symbol, state)
            logger.info("Bracket %s for %s canceled on exchange", bracket_label, symbol)
            try:
                state_service.insert_order(
                    Order(
                        order_id=order_id,
                        client_order_id=order.get("c"),
                        symbol=symbol,
                        side=order.get("S"),
                        type=order_type,
                        status=order_status,
                        price=_safe_float(order.get("p")),
                        quantity=_safe_float(order.get("q")),
                        executed_qty=_safe_float(order.get("l")),
                        avg_price=_safe_float(order.get("ap")),
                        reduce_only=order.get("R") in {True, "true", "TRUE"},
                        time_in_force=order.get("f"),
                        position_side=order.get("ps"),
                        update_time=_safe_int(order.get("T")),
                        cycle_id=state.get("cycle_id"),
                        bracket_label=bracket_label,
                        metadata={"event": "CANCELED"},
                    )
                )
            except Exception:
                logger.exception("Failed to record canceled order %s in DB", order_id)
            return

        if order_status != "FILLED":
            return

        qty = self._extract_filled_quantity(order, state, bracket_label)
        price = self._extract_fill_price(order, state, bracket_label)
        if qty <= 0 or price <= 0:
            logger.debug(
                "Ignoring ORDER_TRADE_UPDATE for %s due to invalid qty/price (qty=%s price=%s)",
                symbol,
                qty,
                price,
            )
            return

        close_type = None
        if bracket_label in {"TP1", "TP2"}:
            close_type = bracket_label
        elif order_type in {"STOP", "STOP_MARKET"}:
            close_type = "SL"

        if close_type:
            logger.info(
                "Recording %s fill via websocket: %s qty=%s price=%s", close_type, symbol, qty, price
            )
            record_partial_close(symbol, qty, price, close_type, order_id=int(order.get("i", 0)))
            try:
                state_service.insert_order(
                    Order(
                        order_id=order_id,
                        client_order_id=order.get("c"),
                        symbol=symbol,
                        side=order.get("S"),
                        type=order_type,
                        status=order_status,
                        price=_safe_float(order.get("p")),
                        quantity=_safe_float(order.get("q")),
                        executed_qty=qty,
                        avg_price=price,
                        reduce_only=order.get("R") in {True, "true", "TRUE"},
                        time_in_force=order.get("f"),
                        position_side=order.get("ps"),
                        update_time=_safe_int(order.get("T")),
                        cycle_id=state.get("cycle_id"),
                        bracket_label=bracket_label or close_type,
                        metadata={"event": "FILLED"},
                    )
                )
            except Exception:
                logger.exception("Failed to record filled order %s in DB", order_id)
        else:
            # Generic reduce-only (manual close) – just adjust local size
            remaining = max(state.get("size", 0) - qty, 0)
            state["size"] = remaining
            state["last_exchange_sync"] = _safe_int(payload.get("E"), int(time.time() * 1000))
            set_position_state(symbol, state)

    def _handle_margin_call(self, payload: Dict[str, Any]) -> None:
        logger.warning("Margin call event received: %s", payload)

    # ------------------------------------------------------------------
    # Helpers for ORDER_TRADE_UPDATE
    # ------------------------------------------------------------------
    def _detect_bracket_label(self, state: Dict[str, Any], order_id: str) -> Optional[str]:
        if not order_id:
            return None
        if str(state.get("tp1_order_id")) == order_id:
            return "TP1"
        if str(state.get("tp2_order_id")) == order_id:
            return "TP2"
        if str(state.get("sl_order_id")) == order_id:
            return "SL"
        return None

    def _extract_filled_quantity(
        self,
        order: Dict[str, Any],
        state: Dict[str, Any],
        bracket_label: Optional[str],
    ) -> float:
        for key in ("l", "z", "q"):
            qty = _safe_float(order.get(key))
            if qty > 0:
                return qty

        if bracket_label == "TP1":
            return _safe_float(state.get("tp1_qty"))
        if bracket_label == "TP2":
            return _safe_float(state.get("tp2_qty"))
        if bracket_label == "SL":
            return _safe_float(state.get("size"))
        return 0.0

    def _extract_fill_price(
        self,
        order: Dict[str, Any],
        state: Dict[str, Any],
        bracket_label: Optional[str],
    ) -> float:
        for key in ("ap", "p", "L", "sp", "avgPrice", "stopPrice"):
            price = _safe_float(order.get(key))
            if price > 0:
                return price

        if bracket_label == "TP1":
            return _safe_float(state.get("tp1_price"))
        if bracket_label == "TP2":
            return _safe_float(state.get("tp2_price"))
        if bracket_label == "SL":
            return _safe_float(state.get("sl_price"))
        return _safe_float(state.get("entry_price"))

    # ------------------------------------------------------------------
    # Public status helpers
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running and bool(self._thread and self._thread.is_alive())

    @property
    def last_event_ts(self) -> int:
        return self._last_event_ts


_LISTENER = AccountStreamListener()


def start_account_listener(force_restart: bool = False) -> bool:
    """Public helper used by supervisors/manage_brackets."""
    return _LISTENER.start(force_restart=force_restart)


def stop_account_listener(timeout: float = 5.0) -> None:
    """Stop the listener (used mainly by tests or shutdown hooks)."""
    _LISTENER.stop(timeout=timeout)


def is_account_listener_active() -> bool:
    """Return True if the account websocket daemon is healthy."""
    return _LISTENER.is_running


def get_account_listener_status() -> Dict[str, Any]:
    """Expose runtime diagnostics for dashboards or CLIs."""
    return {
        "running": _LISTENER.is_running,
        "listen_key": getattr(_LISTENER, "_listen_key", None),
        "listen_key_age": time.time() - getattr(_LISTENER, "_listen_key_obtained", 0.0),
        "last_event_ts": _LISTENER.last_event_ts,
    }


def main():
    """Entry point when running as: python -m services.account_stream_listener"""
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )
    logger.info("Starting AccountStreamListener from command line...")
    started = _LISTENER.start()
    if started:
        logger.info("AccountStreamListener thread started, waiting for connection...")
        # Wait for the thread to actually start running
        time.sleep(2)
        # Keep running until interrupted or thread dies
        try:
            while _LISTENER._thread and _LISTENER._thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down AccountStreamListener...")
            _LISTENER.stop()
    else:
        logger.error("Failed to start AccountStreamListener")
        sys.exit(1)


if __name__ == "__main__":
    main()
