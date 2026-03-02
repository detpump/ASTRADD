# Security Configuration Guide

This directory contains configuration files for the Aster Trading V2 system. 

## Sensitive Files (DO NOT COMMIT)

The following files contain sensitive information and MUST NOT be committed to git:

- `keys.json` - Contains API keys, secrets, and private keys
- Any file with actual credentials instead of environment variable placeholders

## Configuration

### Method 1: Environment Variables (Recommended)

All secrets should be loaded from environment variables. The system automatically reads from:

1. System environment variables
2. `.env` file in the project root (gitignored)

### Method 2: JSON Config Files

If you need to use JSON config files for development:

1. Copy `keys.example.json` to `keys.json`
2. Fill in your actual secrets
3. The `keys.json` file is gitignored and will not be committed

## Required Environment Variables

| Variable | Description |
|----------|-------------|
| `ASTER_API_KEY` | Aster exchange API key (REQUIRED) |
| `ASTER_API_SECRET` | Aster exchange API secret (REQUIRED) |
| `ASTER_PRIVATE_KEY` | Wallet private key (REQUIRED) |
| `ASTER_USER` | User wallet address |
| `ASTER_SIGNER` | Signer wallet address |
| `MINIMAX_API_KEY` | MiniMax LLM API key |
| `ANTHROPIC_API_KEY` | Anthropic/Claude API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `TELEGRAM_ALLOWED_USER_ID` | Allowed Telegram user ID |
| `GATEWAY_AUTH_TOKEN` | OpenClaw gateway auth token |
| `BETTERSTACK_HEARTBEAT_URL` | BetterStack heartbeat URL |

## Setting Up Environment

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in your actual values

3. The `.env` file is automatically gitignored

## Using the Secrets Loader

The `secrets_loader.py` module provides easy access to environment variables:

```python
from src.secrets_loader import get_secrets, secret

# Get a specific secret
api_key = secret('ASTER_API_KEY')

# Get config object
aster_config = get_secrets().get_aster_config()
```

## Security Best Practices

1. NEVER commit secrets to git
2. Rotate API keys regularly
3. Use separate keys for development and production
4. Enable 2FA on exchange accounts
5. Monitor API key usage for anomalies
6. Store private keys securely (consider hardware wallets for large amounts)
