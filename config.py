"""
Configuration module for Jollibee BeeLoyalty System
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Application configuration"""

    # Elasticsearch
    ELASTICSEARCH_ENDPOINT = os.getenv('ELASTICSEARCH_ENDPOINT', 'https://localhost:9200')
    ELASTICSEARCH_API_KEY  = os.getenv('ELASTICSEARCH_API_KEY')

    # Claude via Elastic-managed inference endpoint
    # Uses the pre-provisioned .anthropic-claude-4.5-haiku-completion endpoint.
    # No ANTHROPIC_API_KEY required — Elastic manages the credential.
    CLAUDE_INFERENCE_ID = os.getenv(
        'CLAUDE_INFERENCE_ID',
        '.anthropic-claude-4.5-haiku-completion'   # GA, efficient, completion task
    )

    # Flask
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    FLASK_HOST  = os.getenv('FLASK_HOST', '0.0.0.0')
    FLASK_PORT  = int(os.getenv('FLASK_PORT', 5000))

    # App
    APP_NAME    = os.getenv('APP_NAME',    'Jollibee BeeLoyalty System')
    APP_VERSION = os.getenv('APP_VERSION', '1.0.0')

    # Menu data
    JOLLIBEE_MENU_URL   = os.getenv('JOLLIBEE_MENU_URL', 'https://jollibeemenuprice.ph')
    USE_LIVE_MENU_DATA  = os.getenv('USE_LIVE_MENU_DATA', 'True').lower() == 'true'
    MENU_CACHE_DURATION = int(os.getenv('MENU_CACHE_DURATION', 3600))

    # Elasticsearch indices
    INDEX_MENU         = os.getenv('INDEX_MENU',         'jollibee-menu')
    INDEX_CUSTOMERS    = os.getenv('INDEX_CUSTOMERS',    'jollibee-customers')
    INDEX_TRANSACTIONS = os.getenv('INDEX_TRANSACTIONS', 'jollibee-transactions')
    INDEX_INVENTORY    = os.getenv('INDEX_INVENTORY',    'jollibee-inventory')
    INDEX_STORES       = os.getenv('INDEX_STORES',       'jollibee-stores')
    INDEX_WEATHER      = os.getenv('INDEX_WEATHER',      'jollibee-weather')

    # ELSER
    ELSER_MODEL_ID      = os.getenv('ELSER_MODEL_ID',      '.elser_model_2_linux-x86_64')
    ELSER_PIPELINE_NAME = os.getenv('ELSER_PIPELINE_NAME', 'jollibee-elser-pipeline')

    # Logging
    LOG_LEVEL  = os.getenv('LOG_LEVEL',  'INFO')
    LOG_FORMAT = os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    @classmethod
    def validate(cls):
        required = [
            ('ELASTICSEARCH_ENDPOINT', cls.ELASTICSEARCH_ENDPOINT),
            ('ELASTICSEARCH_API_KEY',  cls.ELASTICSEARCH_API_KEY),
        ]
        missing = [name for name, val in required if not val]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return True
