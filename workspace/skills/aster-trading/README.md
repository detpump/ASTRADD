# Aster Trading System - Validation Package

A comprehensive trading system with advanced validation, security, and monitoring capabilities.

## Overview

The Aster Trading System is an automated trading platform that includes:
- Input validation for trade parameters
- Security checks and protections
- Circuit breaker for API resilience
- Trade deduplication
- Risk management
- Monitoring and debugging utilities

## System Requirements

- Python 3.10+
- macOS or Linux
- PostgreSQL (for trade data persistence)
- Redis (for caching and state management)
- Telegram Bot API (for notifications)

## Installation

### Prerequisites

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root:

```env
# API Keys
ASTER_API_KEY=your_api_key
TELEGRAM_BOT_TOKEN=your_telegram_token

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/aster_trading

# Redis
REDIS_URL=redis://localhost:6379/0

# Trading Configuration
TRADING_MODE=paper  # paper, live
MAX_POSITION_SIZE=10000
RISK_PER_TRADE=0.02
```

## Project Structure

```
aster-trading/
├── src/
│   ├── validation/          # Input validation modules
│   │   ├── input_validator.py
│   │   └── security.py
│   ├── monitoring/          # Monitoring and debugging
│   │   └── debug_utils.py
│   ├── api/                 # API integration
│   ├── risk/                # Risk management
│   ├── execution/           # Trade execution
│   └── data/                # Data handling
├── tests/
│   ├── test_validation.py   # 54+ validation tests
│   ├── test_integration.py  # 15+ integration tests
│   ├── test_circuit_breaker.py
│   └── test_deduplication.py
├── config/
│   ├── risk_config.json
│   └── monitoring_config.json
└── docs/
```

## Usage

### Running the Trading System

```bash
# Start the trading system
python3 launcher.py

# Or use the control script
./control.sh start

# Stop the system
./control.sh stop

# Restart
./control.sh restart
```

### Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_validation.py -v

# Run with coverage
python3 -m pytest tests/ --cov=src --cov-report=html
```

## Validation Modules

### Input Validator (`src/validation/input_validator.py`)

Validates all trade parameters before execution:

```python
from validation.input_validator import InputValidator

validator = InputValidator()

# Validate a trade signal
result = validator.validate_trade_signal({
    'symbol': 'BTC/USD',
    'action': 'BUY',
    'quantity': 0.1,
    'price': 45000.0
})

if result.is_valid:
    print("Trade signal is valid")
else:
    print(f"Validation errors: {result.errors}")
```

### Security Module (`src/validation/security.py`)

Provides security checks and rate limiting:

```python
from validation.security import SecurityValidator

security = SecurityValidator()

# Check if request is allowed
check = security.check_rate_limit("user_id_123")
if check.allowed:
    print("Request allowed")
```

## Monitoring

### Debug Utilities (`src/monitoring/debug_utils.py`)

Tools for monitoring system health:

```python
from monitoring.debug_utils import HealthChecker

health = HealthChecker()
status = health.check_system_health()
print(f"System status: {status}")
```

## Configuration

### Risk Configuration (`config/risk_config.json`)

```json
{
    "max_position_size": 10000,
    "risk_per_trade": 0.02,
    "max_daily_loss": 0.05,
    "circuit_breaker": {
        "failure_threshold": 5,
        "timeout_seconds": 60,
        "success_threshold": 2
    }
}
```

### Monitoring Configuration

Create `config/monitoring_config.json`:

```json
{
    "alerting": {
        "enabled": true,
        "channels": ["telegram", "log"],
        "thresholds": {
            "error_rate": 0.05,
            "latency_ms": 1000
        }
    },
    "metrics": {
        "enabled": true,
        "interval_seconds": 60
    }
}
```

## Troubleshooting

### Common Issues

#### Import Errors

If you encounter import errors, ensure the src directory is in your Python path:

```python
import sys
sys.path.insert(0, 'src')
```

#### Database Connection Issues

Check your DATABASE_URL and ensure PostgreSQL is running:

```bash
# Check PostgreSQL status
pg_isready -h localhost -p 5432

# Or restart
brew services restart postgresql  # macOS
```

#### Test Failures

If tests fail, ensure all dependencies are installed:

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -v --tb=short
```

### Debug Mode

Enable debug logging by setting:

```env
LOG_LEVEL=DEBUG
```

### Health Checks

Run health checks manually:

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
from monitoring.debug_utils import HealthChecker
hc = HealthChecker()
print(hc.check_system_health())
"
```

## Deployment

### Environment-Specific Configuration

Create config files for each environment:

- `config/dev.json` - Development
- `config/staging.json` - Staging
- `config/prod.json` - Production

### Deployment Checklist

1. Run all tests: `python3 -m pytest tests/`
2. Verify configuration files
3. Check environment variables
4. Ensure database migrations are applied
5. Verify monitoring is configured

## Rollback Procedures

### Emergency Rollback

If issues are detected after deployment:

1. **Stop the system**:
   ```bash
   ./control.sh stop
   ```

2. **Restore previous version**:
   ```bash
   git checkout <previous-commit-hash>
   ```

3. **Restart**:
   ```bash
   ./control.sh start
   ```

### Configuration Rollback

To rollback configuration changes:

```bash
# Restore from backup
cp config/risk_config.json.bak config/risk_config.json

# Restart
./control.sh restart
```

### Database Rollback

```bash
# Apply down migrations
alembic downgrade -1
```

## Contributing

### Adding New Validation Rules

1. Add validation logic to `src/validation/input_validator.py`
2. Add corresponding tests in `tests/test_validation.py`
3. Update this README with usage examples

### Code Style

- Follow PEP 8
- Use type hints where possible
- Add docstrings to all public functions

## License

MIT License - See LICENSE file for details

## Support

For issues and questions:
- Open an issue on GitHub
- Check the logs in `logs/` directory
- Run with `LOG_LEVEL=DEBUG` for detailed output
