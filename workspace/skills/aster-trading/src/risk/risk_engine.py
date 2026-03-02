#!/usr/bin/env python3
import json
import os
from datetime import datetime

# Use centralized paths - single point of change for server migration
try:
    from paths import BASE_DIR, STATE_DIR, CONFIG_DIR
    BASE = BASE_DIR
except ImportError:
    # Fallback for when paths.py is not available - use environment variable or default
    BASE = os.environ.get("ASTER_TRADING_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATE_PATH = os.path.join(STATE_DIR if 'STATE_DIR' in dir() else os.path.join(BASE, "data/state"), "trade_state.json")
RULES_PATH = os.path.join(BASE, "config/risk_rules.md")
CONFIG_PATH = os.path.join(CONFIG_DIR if 'CONFIG_DIR' in dir() else os.path.join(BASE, "config"), "risk_config.json")

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    state = load_json(STATE_PATH)
    rules_text = ""
    if os.path.exists(RULES_PATH):
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            rules_text = f.read()

    current_config = load_json(CONFIG_PATH)

    # AQUÍ es donde tu cliente IA debe decidir new_config.
    # Por ahora, simplemente usamos current_config tal cual.
    new_config = current_config

    new_config.setdefault("mode", {})
    new_config["mode"]["last_update"] = datetime.utcnow().isoformat() + "Z"
    new_config["mode"]["risk_profile"] = new_config["mode"].get("risk_profile", "normal")
    new_config["mode"]["reason"] = new_config["mode"].get(
        "reason",
        "config actualizada por motor de riesgo externo"
    )

    save_json(CONFIG_PATH, new_config)
    print(json.dumps({
        "status": "ok",
        "symbols": list(new_config.get("symbols", {}).keys()),
        "config": new_config,
        "state_keys": list(state.keys()),
        "rules_len": len(rules_text)
    }, indent=2))

if __name__ == "__main__":
    main()
