from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

from flask import jsonify, request

from dashboard.context import BASE_DIR, load_state, save_state
from dashboard.server import SUBSYSTEMS, SYSTEM_INIT_LOG, app, system_control


def _append_db_subsystem_event(subsystem: str, action: str, status: str, message: str = "", metadata: dict | None = None):
    try:
        from state.state_service import state_service
        from state.models import SystemComponent, SystemActivity

        now_ms = int(time.time() * 1000)
        component = SystemComponent(
            name=subsystem,
            status=status.upper(),
            last_action=action,
            last_message=message,
            metadata=metadata or {},
            updated_at=now_ms,
        )
        state_service.upsert_system_component(component)
        activity = SystemActivity(
            subsystem=subsystem,
            action=action,
            status=status.upper(),
            message=message,
            created_at=now_ms,
        )
        state_service.log_system_activity(activity)
    except Exception:
        pass

def _append_system_init_log(subsystem: str, action: str, status: str, message: str = "", metadata: dict | None = None):
    event = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'subsystem': subsystem,
        'action': action,
        'status': status,
        'message': message,
    }
    try:
        with SYSTEM_INIT_LOG.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:
        pass
    _append_db_subsystem_event(subsystem, action, status, message, metadata=metadata)


def _command_response(result: dict, subsystem: str, action: str, metadata: dict | None = None):
    status = 'success' if result.get('success') else 'error'
    stdout_preview = (result.get('stdout') or '')[:500]
    _append_system_init_log(subsystem, action, status, stdout_preview, metadata=metadata)
    payload = {
        'success': result.get('success', False),
        'stdout': (result.get('stdout') or '')[:2000],
        'stderr': (result.get('stderr') or '')[:2000],
        'returncode': result.get('returncode', -1),
    }
    # Always include metadata to show PID info to user
    if metadata:
        payload['metadata'] = metadata
    if result.get('success'):
        return jsonify(payload)
    return jsonify(payload), 500


def _control_subsystem(name: str):
    data = request.get_json(silent=True) or {}
    action = data.get('action', 'status').lower()
    action = action if action in {'start', 'stop', 'status'} else 'status'
    cmd = f'./system_control.sh {name} {action}'
    timeout = 60 if name == 'trading' else 40
    
    # Execute command directly instead of calling _run_command
    try:
        result = subprocess.run(
            ['bash', '-c', f'cd {BASE_DIR} && {cmd}'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result_dict = {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
        }
    except subprocess.TimeoutExpired as exc:
        result_dict = {
            'success': False,
            'stdout': exc.stdout or '',
            'stderr': f'Timeout: {exc}',
            'returncode': -1,
        }
    except Exception as exc:
        result_dict = {
            'success': False,
            'stdout': '',
            'stderr': str(exc),
            'returncode': -1,
        }
    
    if name == 'trading' and action in {'start', 'stop'}:
        system_control['running'] = action == 'start' and result_dict.get('success')
        if system_control['running']:
            system_control['start_time'] = int(time.time() * 1000)
        state = load_state()
        state['running'] = system_control['running']
        state['timestamp'] = int(time.time() * 1000)
        save_state(state)
    stdout_preview = (result_dict.get('stdout') or '')[:500]
    stderr_preview = (result_dict.get('stderr') or '')[:500]
    component_metadata = {
        'command': cmd,
        'returncode': result_dict.get('returncode'),
        'stdout_preview': stdout_preview,
        'stderr_preview': stderr_preview,
        'timestamp': int(time.time() * 1000),
    }
    if name == 'trading':
        component_metadata['pid_file'] = str(Path(BASE_DIR) / 'v2_process.pid')
        component_metadata['log'] = str(Path(BASE_DIR) / 'logs' / 'v2_output.log')
    elif name == 'account-listener':
        component_metadata['pid_file'] = str(Path(BASE_DIR) / 'account_listener.pid')
        component_metadata['log'] = str(Path(BASE_DIR) / 'logs' / 'account_stream.log')
        # Read actual PID if available
        pid_file = Path(BASE_DIR) / 'account_listener.pid'
        if pid_file.exists():
            try:
                component_metadata['actual_pid'] = int(pid_file.read_text().strip())
            except:
                pass
    return _command_response(result_dict, name, action, metadata=component_metadata)
