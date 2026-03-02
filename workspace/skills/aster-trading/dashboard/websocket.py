from __future__ import annotations

import threading


def register_socketio_handlers(socketio, emit, load_state_fn, get_anomalies_internal_fn):
    @socketio.on('connect')
    def handle_connect():
        """Handle client connection"""
        print('Client connected')
        emit('connected', {'status': 'connected'})

    @socketio.on('disconnect')
    def handle_disconnect():
        """Handle client disconnection"""
        print('Client disconnected')

    @socketio.on('request_update')
    def handle_update_request(data):
        """Handle request for immediate update"""
        try:
            # Send current state
            state = load_state_fn()
            emit('state_update', state)
        except Exception as e:
            emit('error', {'message': str(e)})

    def broadcast_updates():
        """Background thread to broadcast periodic updates"""
        while True:
            try:
                socketio.sleep(3)  # Broadcast every 3 seconds

                # Get current state
                state = load_state_fn()

                # Broadcast state update
                socketio.emit('state_update', state, namespace='/')

                # Broadcast anomalies
                anomalies = get_anomalies_internal_fn()
                socketio.emit('anomalies_update', anomalies, namespace='/')

            except Exception as e:
                print(f"Broadcast error: {e}")

    broadcast_thread = threading.Thread(target=broadcast_updates, daemon=True)
    broadcast_thread.start()

