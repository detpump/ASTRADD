#!/usr/bin/env python3
"""
Mock Aster Futures API Server for Testnet Testing.

This module provides a mock implementation of the Aster Futures API
for testing purposes, completely isolated from production systems.
"""

import json
import time
import random
import hashlib
import hmac
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_api_server")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class MockPosition:
    """Mock position data"""
    symbol: str
    position_amt: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    position_side: str = "LONG"


@dataclass
class MockOrder:
    """Mock order data"""
    order_id: int
    symbol: str
    side: str  # BUY or SELL
    order_type: str  # MARKET, LIMIT, STOP, TAKE_PROFIT
    price: float
    orig_qty: float
    executed_qty: float
    status: str  # NEW, FILLED, PARTIALLY_FILLED, CANCELLED, REJECTED
    time_in_force: str = "GTC"
    stop_price: Optional[float] = None
    update_time: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class MockBalance:
    """Mock balance data"""
    asset: str
    balance: float
    cross_wallet_balance: float
    cross_unrealized_pnl: float


# =============================================================================
# Mock API Server State
# =============================================================================

class MockAPIServerState:
    """Maintains the state of the mock API server"""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.orders: Dict[int, MockOrder] = {}
        self.positions: Dict[str, MockPosition] = {}
        self.balances: Dict[str, MockBalance] = {
            "USDT": MockBalance(
                asset="USDT",
                balance=10000.0,
                cross_wallet_balance=10000.0,
                cross_unrealized_pnl=0.0
            )
        }
        self.order_id_counter = 1000
        self.next_price = 42000.0
        self.price_history: Dict[str, List[float]] = {}
        
        # Error simulation
        self.error_injection_enabled = False
        self.error_type: Optional[str] = None
        self.error_status_code: Optional[int] = None
        self.latency_ms: int = 0
        
        # Rate limiting
        self.request_count = 0
        self.rate_limit_window = 60  # seconds
        self.rate_limit_max = 1200
        
    def reset(self):
        """Reset all state"""
        with self.lock:
            self.orders.clear()
            self.positions.clear()
            self.balances = {
                "USDT": MockBalance(
                    asset="USDT",
                    balance=10000.0,
                    cross_wallet_balance=10000.0,
                    cross_unrealized_pnl=0.0
                )
            }
            self.order_id_counter = 1000
            self.request_count = 0


# Global server state
_server_state = MockAPIServerState()


# =============================================================================
# Price Simulation
# =============================================================================

def generate_kline_data(
    symbol: str,
    interval: str,
    limit: int,
    start_price: float = 42000.0
) -> List[List[Any]]:
    """Generate mock kline (candlestick) data"""
    
    # Use stored price or start fresh
    if symbol not in _server_state.price_history:
        _server_state.price_history[symbol] = []
    
    prices = _server_state.price_history[symbol]
    
    # Generate prices if needed
    if len(prices) < limit:
        current = prices[-1] if prices else start_price
        for _ in range(limit - len(prices)):
            # Random walk with slight upward bias
            change_pct = random.uniform(-0.002, 0.003)
            current = current * (1 + change_pct)
            prices.append(current)
    
    # Get the last 'limit' prices
    prices = prices[-limit:]
    
    # Convert to kline format
    klines = []
    base_time = int(time.time() * 1000) - (limit * 60000)  # 1 minute intervals
    
    for i, close_price in enumerate(prices):
        open_price = close_price * random.uniform(0.998, 1.002)
        high_price = max(open_price, close_price) * random.uniform(1.0, 1.005)
        low_price = min(open_price, close_price) * random.uniform(0.995, 1.0)
        volume = random.uniform(100, 1000)
        
        klines.append([
            base_time + (i * 60000),  # open_time
            str(open_price),
            str(high_price),
            str(low_price),
            str(close_price),
            str(volume),
            base_time + ((i + 1) * 60000),  # close_time
            str(volume),
            100,  # number of trades
            str(volume * 0.5),
            str(volume * close_price),
            0  # taker buy volume
        ])
    
    return klines


def get_current_price(symbol: str) -> float:
    """Get simulated current price"""
    if symbol in _server_state.price_history and _server_state.price_history[symbol]:
        return _server_state.price_history[symbol][-1]
    return 42000.0


# =============================================================================
# API Endpoint Handlers
# =============================================================================

def handle_exchange_info(params: Dict) -> Dict:
    """Handle /fapi/v1/exchangeInfo"""
    return {
        "timezone": "UTC",
        "serverTime": int(time.time() * 1000),
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "pair": "BTCUSDT",
                "contractType": "PERPETUAL",
                "deliveryDate": 4133404800000,
                "status": "TRADING",
                "maintMarginPercent": "2.5000",
                "requiredMarginPercent": "5.0000",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "maxQuantity": "100.000",
                "minQuantity": "0.001",
            }
        ]
    }


def handle_klines(params: Dict) -> List[List[Any]]:
    """Handle /fapi/v1/klines"""
    symbol = params.get("symbol", "BTCUSDT")
    interval = params.get("interval", "1m")
    limit = int(params.get("limit", 100))
    limit = min(limit, 1000)
    
    return generate_kline_data(symbol, interval, limit)


def handle_ticker_price(params: Dict) -> Dict:
    """Handle /fapi/v1/ticker/price"""
    symbol = params.get("symbol", "BTCUSDT")
    price = get_current_price(symbol)
    
    return {
        "symbol": symbol,
        "price": str(price)
    }


def handle_premium_index(params: Dict) -> Dict:
    """Handle /fapi/v1/premiumIndex"""
    symbol = params.get("symbol", "BTCUSDT")
    price = get_current_price(symbol)
    
    return {
        "symbol": symbol,
        "markPrice": str(price),
        "indexPrice": str(price * 0.999),
        "estimatedSettlePrice": str(price * 0.998),
        "lastFundingRate": "0.0001",
        "nextFundingTime": int(time.time() * 1000) + 28800000,
        "interestRate": "0.0003",
        "time": int(time.time() * 1000)
    }


def handle_balance(params: Dict) -> Dict:
    """Handle /fapi/v1/balance"""
    with _server_state.lock:
        balances = []
        for bal in _server_state.balances.values():
            balances.append({
                "asset": bal.asset,
                "balance": str(bal.balance),
                "crossWalletBalance": str(bal.cross_wallet_balance),
                "crossUnrealizedPnl": str(bal.cross_unrealized_pnl)
            })
        return {"assets": balances}


def handle_positions(params: Dict) -> List[Dict]:
    """Handle /fapi/v1/positionRisk"""
    with _server_state.lock:
        positions = []
        for pos in _server_state.positions.values():
            positions.append({
                "symbol": pos.symbol,
                "positionAmt": str(pos.position_amt),
                "entryPrice": str(pos.entry_price),
                "markPrice": str(pos.mark_price),
                "unRealizedProfit": str(pos.unrealized_pnl),
                "leverage": str(pos.leverage),
                "positionSide": pos.position_side
            })
        
        # Always return at least one position entry for BTCUSDT
        if not positions:
            current_price = get_current_price("BTCUSDT")
            positions.append({
                "symbol": "BTCUSDT",
                "positionAmt": "0.000",
                "entryPrice": "0.00",
                "markPrice": str(current_price),
                "unRealizedProfit": "0.00",
                "leverage": "10",
                "positionSide": "BOTH"
            })
        
        return positions


def handle_place_order(params: Dict) -> Dict:
    """Handle POST /fapi/v1/order"""
    with _server_state.lock:
        # Generate order ID
        order_id = _server_state.order_id_counter
        _server_state.order_id_counter += 1
        
        symbol = params.get("symbol", "BTCUSDT")
        side = params.get("side", "BUY")
        order_type = params.get("type", "MARKET")
        quantity = float(params.get("quantity", 0))
        price = float(params.get("price", 0)) if params.get("price") else 0
        stop_price = float(params.get("stopPrice", 0)) if params.get("stopPrice") else None
        
        current_price = get_current_price(symbol)
        
        # Determine execution
        if order_type == "MARKET":
            # Market orders fill immediately
            executed_qty = quantity
            status = "FILLED"
            fill_price = current_price
        else:
            # Limit orders wait
            executed_qty = 0
            status = "NEW"
            fill_price = price
        
        # Create mock order
        order = MockOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=fill_price if order_type == "MARKET" else price,
            orig_qty=quantity,
            executed_qty=executed_qty,
            status=status,
            stop_price=stop_price
        )
        
        _server_state.orders[order_id] = order
        
        # If filled, update position
        if status == "FILLED":
            _update_position_after_fill(symbol, side, executed_qty, fill_price)
        
        return {
            "orderId": order_id,
            "symbol": symbol,
            "status": status,
            "side": side,
            "type": order_type,
            "price": str(fill_price),
            "origQty": str(quantity),
            "executedQty": str(executed_qty),
            "timeInForce": "GTC",
            "updateTime": int(time.time() * 1000)
        }


def _update_position_after_fill(symbol: str, side: str, quantity: float, price: float):
    """Update position after order fill"""
    with _server_state.lock:
        if symbol not in _server_state.positions:
            _server_state.positions[symbol] = MockPosition(
                symbol=symbol,
                position_amt=0,
                entry_price=0,
                mark_price=price,
                unrealized_pnl=0,
                leverage=10
            )
        
        pos = _server_state.positions[symbol]
        
        if side == "BUY":
            pos.position_amt += quantity
            if pos.position_amt > 0 and pos.entry_price == 0:
                pos.entry_price = price
        else:
            pos.position_amt -= quantity
            if pos.position_amt < 0 and pos.entry_price == 0:
                pos.entry_price = price
        
        pos.mark_price = price
        pos.unrealized_pnl = (pos.mark_price - pos.entry_price) * abs(pos.position_amt)


def handle_get_open_orders(params: Dict) -> List[Dict]:
    """Handle /fapi/v1/openOrders"""
    with _server_state.lock:
        symbol = params.get("symbol")
        
        orders = []
        for order in _server_state.orders.values():
            if order.status in ("NEW", "PARTIALLY_FILLED"):
                if symbol is None or order.symbol == symbol:
                    orders.append({
                        "orderId": order.order_id,
                        "symbol": order.symbol,
                        "status": order.status,
                        "side": order.side,
                        "type": order.order_type,
                        "price": str(order.price),
                        "origQty": str(order.orig_qty),
                        "executedQty": str(order.executed_qty)
                    })
        return orders


def handle_cancel_order(params: Dict) -> Dict:
    """Handle DELETE /fapi/v1/order"""
    with _server_state.lock:
        symbol = params.get("symbol")
        order_id = int(params.get("orderId", 0))
        
        if order_id in _server_state.orders:
            order = _server_state.orders[order_id]
            order.status = "CANCELLED"
            
            return {
                "orderId": order_id,
                "symbol": symbol,
                "status": "CANCELLED",
                "side": order.side,
                "type": order.order_type
            }
        
        return {"error": "Order not found"}


def handle_set_leverage(params: Dict) -> Dict:
    """Handle POST /fapi/v1/leverage"""
    symbol = params.get("symbol", "BTCUSDT")
    leverage = int(params.get("leverage", 10))
    
    with _server_state.lock:
        if symbol in _server_state.positions:
            _server_state.positions[symbol].leverage = leverage
        else:
            _server_state.positions[symbol] = MockPosition(
                symbol=symbol,
                position_amt=0,
                entry_price=0,
                mark_price=get_current_price(symbol),
                unrealized_pnl=0,
                leverage=leverage
            )
    
    return {
        "symbol": symbol,
        "leverage": leverage,
        "maxNotional": str(leverage * 100000)
    }


def handle_account_trades(params: Dict) -> List[Dict]:
    """Handle /fapi/v1/accountTrades"""
    # Return mock trade history
    return [
        {
            "tradeId": 1000 + i,
            "symbol": "BTCUSDT",
            "side": "BUY",
            "price": str(42000 + i * 10),
            "qty": "0.001",
            "commission": "0.001",
            "time": int(time.time() * 1000) - (i * 60000)
        }
        for i in range(10)
    ]


# =============================================================================
# Request Router
# =============================================================================

ENDPOINT_HANDLERS = {
    "/fapi/v1/exchangeInfo": handle_exchange_info,
    "/fapi/v1/klines": handle_klines,
    "/fapi/v1/ticker/price": handle_ticker_price,
    "/fapi/v1/premiumIndex": handle_premium_index,
    "/fapi/v1/balance": handle_balance,
    "/fapi/v1/positionRisk": handle_positions,
    "/fapi/v1/order": handle_place_order,
    "/fapi/v1/openOrders": handle_get_open_orders,
    "/fapi/v1/allOpenOrders": handle_get_open_orders,
    "/fapi/v1/leverage": handle_set_leverage,
    "/fapi/v1/accountTrades": handle_account_trades,
}


# =============================================================================
# HTTP Request Handler
# =============================================================================

class MockAPIRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mock API"""
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass
    
    def do_GET(self):
        """Handle GET requests"""
        self._handle_request("GET")
    
    def do_POST(self):
        """Handle POST requests"""
        self._handle_request("POST")
    
    def do_DELETE(self):
        """Handle DELETE requests"""
        self._handle_request("DELETE")
    
    def _handle_request(self, method: str):
        """Process request and generate response"""
        # Apply latency if configured
        if _server_state.latency_ms > 0:
            time.sleep(_server_state.latency_ms / 1000)
        
        # Check for error injection
        if _server_state.error_injection_enabled:
            if _server_state.error_status_code:
                self.send_response(_server_state.error_status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Injected error"}).encode())
                return
        
        # Parse URL and parameters
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        
        # Get handler
        handler = ENDPOINT_HANDLERS.get(path)
        
        if handler is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode())
            return
        
        # Execute handler
        try:
            if method in ("POST", "DELETE"):
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length > 0:
                    body = self.rfile.read(content_length)
                    body_params = {k: v for k, v in parse_qs(body.decode()).items()}
                    params.update(body_params)
            
            # Call handler
            result = handler(params)
            
            # Send response
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            response = json.dumps(result)
            self.wfile.write(response.encode())
            
        except Exception as e:
            logger.error(f"Handler error: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


# =============================================================================
# Server Control
# =============================================================================

_server_instance: Optional[HTTPServer] = None


def start_server(port: int = 8899) -> HTTPServer:
    """Start the mock API server"""
    global _server_instance
    
    if _server_instance is not None:
        return _server_instance
    
    _server_instance = HTTPServer(("localhost", port), MockAPIRequestHandler)
    logger.info(f"Mock API server started on port {port}")
    
    # Run in background thread
    thread = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    thread.start()
    
    return _server_instance


def stop_server():
    """Stop the mock API server"""
    global _server_instance
    
    if _server_instance:
        _server_instance.shutdown()
        _server_instance = None
        logger.info("Mock API server stopped")


def get_state() -> MockAPIServerState:
    """Get the server state for testing"""
    return _server_state


def reset_state():
    """Reset the server state"""
    _server_state.reset()


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import sys
    
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    
    print(f"Starting Mock API Server on port {port}...")
    server = start_server(port)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        stop_server()
