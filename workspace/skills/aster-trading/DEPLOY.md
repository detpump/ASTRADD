# Aster Trading System V2 - Deployment Guide

Complete guide for deploying the Aster Trading System on a fresh macOS installation.

## Quick Start (TL;DR)

```bash
# 1. Clone/copy project
cd /Users/FIRMAS/.openclaw/workspace/skills
git clone <repo> aster-trading

# 2. Install dependencies
cd aster-trading
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env  # Edit with your API keys

# 4. Start system
./system_control.sh start_all

# 5. Enable auto-start on boot (macOS)
cp com.aster.tradingv2.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aster.tradingv2.plist
```

---

## 1. Prerequisites

### 1.1 System Requirements

- **OS**: macOS 12.0+ (Monterey or later)
- **Python**: 3.10+
- **RAM**: 4GB minimum, 8GB recommended
- **Disk**: 500MB free space

### 1.2 Required Software

```bash
# Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.10+
brew install python@3.11

# Install system dependencies
brew install jq curl wget
```

---

## 2. Project Structure

```
/Users/FIRMAS/.openclaw/
├── .venv/                    # Python virtual environment
├── workspace/
│   └── skills/
│       └── aster-trading/    # Main project directory
│           ├── src/           # Source code
│           │   ├── api/       # API integration
│           │   ├── risk/      # Risk management
│           │   ├── execution/ # Trade execution
│           │   ├── data/      # Data handling
│           │   └── monitoring/# Health metrics
│           ├── tests/         # Test suite
│           ├── config/        # Configuration files
│           ├── logs/          # Log files
│           ├── docs/          # Documentation
│           ├── bin/           # Helper scripts
│           ├── system_control.sh    # Main control script
│           ├── control.sh           # Legacy control
│           ├── launcher.py          # Python launcher
│           ├── start_v2.sh          # V2 startup script
│           ├── autostart_aster_trading.sh  # Auto-start script
│           └── com.aster.tradingv2.plist   # macOS LaunchAgent
├── logs/                     # OpenClaw logs
├── cron/                     # Cron jobs
└── .env                      # Environment variables
```

---

## 3. Installation Steps

### 3.1 Create Virtual Environment

```bash
cd /Users/FIRMAS/.openclaw
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2 Install Dependencies

```bash
# Navigate to project
cd workspace/skills/aster-trading

# Install Python packages
pip install -r requirements.txt

# Verify installation
python3 -c "import ccxt; import pandas; print('Dependencies OK')"
```

### 3.3 Environment Configuration

Create `.env` file in `/Users/FIRMAS/.openclaw/`:

```env
# API Configuration
ASTER_API_KEY=your_aster_api_key
ASTER_API_SECRET_PATH=/Users/FIRMAS/.openclaw/credentials/aster_api_secret.txt

# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# OpenClaw
OPENCLAW_GATEWAY_URL=http://localhost:8080

# Trading Mode
TRADING_MODE=paper  # paper or live
```

### 3.4 API Credentials

Create the API secret file:

```bash
mkdir -p /Users/FIRMAS/.openclaw/credentials
echo "your_api_secret_here" > /Users/FIRMAS/.openclaw/credentials/aster_api_secret.txt
chmod 600 /Users/FIRMAS/.openclaw/credentials/aster_api_secret.txt
```

---

## 4. System Control

### 4.1 Main Control Script

Use [`system_control.sh`](workspace/skills/aster-trading/system_control.sh) for all operations:

```bash
cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading

# Start everything (recommended)
./system_control.sh start_all

# Stop everything
./system_control.sh stop_all

# Restart
./system_control.sh restart

# Check status
./system_control.sh status

# Monitoring commands
./system_control.sh monitor    # Full dashboard
./system_control.sh equity     # Quick equity check
./system_control.sh failures   # Recent errors
./system_control.sh logs       # Tail recent logs
```

### 4.2 Legacy Control Script

The [`control.sh`](workspace/skills/aster-trading/control.sh) provides additional controls:

```bash
./control.sh start    # Start trading
./control.sh stop     # Stop trading
./control.sh restart  # Restart
./control.sh status   # Show status
```

---

## 5. Auto-Start Configuration (macOS)

### 5.1 LaunchAgent Setup

The system includes [`com.aster.tradingv2.plist`](workspace/skills/aster-trading/com.aster.tradingv2.plist) for macOS auto-start:

```bash
# Copy to LaunchAgents
cp /Users/FIRMAS/.openclaw/workspace/skills/aster-trading/com.aster.tradingv2.plist ~/Library/LaunchAgents/

# Load the agent
launchctl load ~/Library/LaunchAgents/com.aster.tradingv2.plist

# Verify it's loaded
launchctl list | grep aster

# Unload (if needed)
launchctl unload ~/Library/LaunchAgents/com.aster.tradingv2.plist
```

### 5.2 Verify Auto-Start

After reboot, check if system started:

```bash
# Check system status
cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading
./system_control.sh status
```

---

## 6. Testing

### 6.1 Run Test Suite

```bash
cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading
python3 -m pytest tests/ -v --tb=short
```

### 6.2 Circuit Breaker Tests

```bash
python3 -m pytest tests/test_circuit_breaker.py -v
```

### 6.3 Deduplication Tests

```bash
python3 -m pytest tests/test_deduplication.py -v
```

---

## 7. Monitoring

### 7.1 Telegram Commands

After setting up Telegram bot, you can use these commands:

| Command | Description |
|---------|-------------|
| `HB Trading` or `heartbeat` | Trigger heartbeat (every 30 min) |
| `reporte` or `reporte trading` | Trading report (every 2h) |
| `events` or `eventos` | System events (every 2h) |
| `estado del sistema` | Full system status |
| `equity` | Current equity |
| `posiciones` | Open positions |

### 7.2 Dashboard Commands (from terminal)

```bash
./system_control.sh monitor    # Full real-time dashboard
./system_control.sh equity     # Equity + P&L + Drawdown
./system_control.sh failures   # Recent errors
./system_control.sh logs       # Recent log entries
```

---

## 8. State Files

### 8.1 Key State Files

| File | Purpose |
|------|---------|
| `logs/v2_state.json` | Trading system state |
| `logs/risk_state.json` | Risk management state |
| `logs/risk_errors.jsonl` | Risk-related errors |
| `logs/failures.jsonl` | System failures |

### 8.2 Viewing State

```bash
# Full state
cat logs/v2_state.json | jq '.'

# Just running status
cat logs/v2_state.json | jq '{running, mode, equity, daily_pnl}'

# Equity only
./system_control.sh equity
```

---

## 9. Migration Checklist

Use this checklist when moving to a new Mac:

### 9.1 Before Migration

- [ ] Export current state files
- [ ] Note current positions (if any)
- [ ] Record API credentials
- [ ] Document Telegram bot token

### 9.2 On New Mac

- [ ] Install prerequisites (Python, Homebrew)
- [ ] Copy project directory
- [ ] Create virtual environment
- [ ] Install dependencies
- [ ] Configure `.env` file
- [ ] Set up API credentials
- [ ] Test with `./system_control.sh start_all`
- [ ] Verify monitoring works
- [ ] Set up LaunchAgent for auto-start
- [ ] Test system restart

### 9.3 Verification

```bash
# Start system
cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading
./system_control.sh start_all

# Wait 10 seconds
sleep 10

# Check status
./system_control.sh status

# Check dashboard
./system_control.sh monitor

# Check equity
./system_control.sh equity
```

Expected output should show:
- `running: true`
- `mode: continuous`
- Equity value
- Open positions

---

## 10. Troubleshooting

### 10.1 System Won't Start

```bash
# Check for errors
./system_control.sh logs

# Check process
ps aux | grep python | grep aster

# Check port conflicts
lsof -i :8080
```

### 10.2 Import Errors

```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall

# Check Python path
python3 -c "import sys; print(sys.path)"
```

### 10.3 API Connection Issues

```bash
# Test API connection
python3 -c "import ccxt; print(ccxt.aster().fetch_time())"

# Check circuit breaker
cat logs/v2_state.json | jq '.circuit_breaker'
```

---

## 11. Configuration Files

### 11.1 Risk Configuration

Edit [`config/risk_config.json`](workspace/skills/aster-trading/config/risk_config.json):

```json
{
  "global": {
    "max_position_size": 10000,
    "risk_per_trade": 0.02
  },
  "symbols": {
    "ETHUSDT": { ... },
    "SOLUSDT": { ... }
  }
}
```

### 11.2 Cron Jobs

Configure reporting in `cron/jobs.json`. The system uses:
- Heartbeat: Every 30 minutes
- Reports: Every 2 hours
- Events: Every 2 hours

---

## 12. Security Notes

1. **Never commit** `.env` or credentials to git
2. **Use file permissions**: `chmod 600` on credential files
3. **API keys** should be in a secure location
4. **Telegram tokens** should be kept private
5. **Review** [`CODE_ANALYSIS_REPORT.md`](workspace/skills/aster-trading/CODE_ANALYSIS_REPORT.md) for security issues

---

## 13. Files to Back Up

For disaster recovery, back up:

```bash
# Essential files
/Users/FIRMAS/.openclaw/.env
/Users/FIRMAS/.openclaw/credentials/
/Users/FIRMAS/.openclaw/workspace/skills/aster-trading/config/
/Users/FIRMAS/.openclaw/workspace/skills/aster-trading/logs/

# Optional: entire project
/Users/FIRMAS/.openclaw/workspace/skills/aster-trading/
```

---

## 14. Quick Reference

```bash
# Start everything
cd /Users/FIRMAS/.openclaw/workspace/skills/aster-trading
./system_control.sh start_all

# Check status
./system_control.sh status

# Monitor dashboard
./system_control.sh monitor

# Stop everything
./system_control.sh stop_all

# View logs
tail -f logs/aster_trading.log
```

---

**Last Updated**: 2026-02-26
**Version**: 2.0
