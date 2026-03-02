#!/usr/bin/env python3
"""
Secure Secrets Loader for Aster Trading V2

This module provides a secure way to load secrets from environment variables
instead of storing them in JSON config files.

All secrets should be stored in .env file (which is gitignored) or
system environment variables.
"""
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Try to import dotenv for .env file loading
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False
    logger.warning("python-dotenv not installed. Install with: pip install python-dotenv")


class SecretsLoader:
    """
    Centralized secrets loader that reads from environment variables.
    This avoids storing sensitive credentials in config files.
    """
    
    _instance: Optional['SecretsLoader'] = None
    _secrets: Optional[Dict[str, Any]] = None
    
    # Define required and optional secrets
    REQUIRED_SECRETS = [
        'ASTER_API_KEY',
        'ASTER_API_SECRET',
        'ASTER_PRIVATE_KEY',
    ]
    
    OPTIONAL_SECRETS = [
        'ASTER_USER',
        'ASTER_SIGNER',
        'MINIMAX_API_KEY',
        'ANTHROPIC_API_KEY',
        'TELEGRAM_BOT_TOKEN',
        'TELEGRAM_CHAT_ID',
        'BETTERSTACK_HEARTBEAT_URL',
        'ASTER_FAPI_BASE',
    ]
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._secrets is None:
            self._load_secrets()
    
    def _load_secrets(self):
        """Load secrets from environment variables and .env file"""
        self._secrets = {}
        
        # Try to load from .env file
        if DOTENV_AVAILABLE:
            # Look for .env in project root
            env_paths = [
                Path(__file__).parent.parent.parent.parent / '.env',
                Path(__file__).parent.parent.parent / '.env',
                Path('.env'),
            ]
            for env_path in env_paths:
                if env_path.exists():
                    load_dotenv(env_path)
                    logger.info(f"Loaded .env from {env_path}")
                    break
        
        # Load required secrets
        for key in self.REQUIRED_SECRETS:
            value = os.environ.get(key)
            if value:
                self._secrets[key] = value
            else:
                logger.warning(f"Required secret {key} not found in environment")
        
        # Load optional secrets
        for key in self.OPTIONAL_SECRETS:
            self._secrets[key] = os.environ.get(key)
        
        logger.info("Secrets loaded from environment variables")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a secret by key name"""
        return self._secrets.get(key, default)
    
    def get_aster_config(self) -> Dict[str, Any]:
        """Get Aster exchange configuration"""
        return {
            'api_key': self.get('ASTER_API_KEY'),
            'api_secret': self.get('ASTER_API_SECRET'),
            'user': self.get('ASTER_USER'),
            'signer': self.get('ASTER_SIGNER'),
            'private_key': self.get('ASTER_PRIVATE_KEY'),
            'testnet': os.environ.get('ASTER_TESTNET', 'false').lower() == 'true',
        }
    
    def get_llm_config(self) -> Dict[str, Any]:
        """Get LLM provider configuration"""
        return {
            'provider': os.environ.get('LLM_PROVIDER', 'minimax'),
            'api_key': self.get('MINIMAX_API_KEY') or self.get('ANTHROPIC_API_KEY'),
            'model': os.environ.get('LLM_MODEL', 'MiniMax-M2.1'),
        }
    
    def get_telegram_config(self) -> Dict[str, Any]:
        """Get Telegram configuration"""
        return {
            'bot_token': self.get('TELEGRAM_BOT_TOKEN'),
            'chat_id': self.get('TELEGRAM_CHAT_ID'),
        }
    
    def validate(self) -> bool:
        """Validate that all required secrets are present"""
        missing = []
        for key in self.REQUIRED_SECRETS:
            if not self.get(key):
                missing.append(key)
        
        if missing:
            logger.error(f"Missing required secrets: {missing}")
            return False
        
        return True


# Global instance
_secrets_loader: Optional[SecretsLoader] = None


def get_secrets() -> SecretsLoader:
    """Get or create global secrets loader"""
    global _secrets_loader
    if _secrets_loader is None:
        _secrets_loader = SecretsLoader()
    return _secrets_loader


def secret(key: str, default: Any = None) -> Any:
    """Quick access to secret values"""
    return get_secrets().get(key, default)


if __name__ == "__main__":
    # Test - print loaded secrets (masked)
    secrets = get_secrets()
    print("Loaded secrets:")
    for key in secrets._secrets:
        value = secrets._secrets[key]
        if value:
            masked = value[:8] + "..." if len(value) > 8 else "***"
            print(f"  {key}: {masked}")
    
    print(f"\nValidation: {'PASSED' if secrets.validate() else 'FAILED'}")
