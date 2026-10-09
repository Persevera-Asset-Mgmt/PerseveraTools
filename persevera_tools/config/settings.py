import os
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv
from .defaults import DEFAULT_CONFIG

class Settings:
    def __init__(self):
        # Load environment variables from .env file if it exists
        env_path = Path.home() / '.persevera' / '.env'
        load_dotenv(env_path)
        
        self._load_config()

    def _load_config(self):
        # Load paths
        self.DATA_PATH = os.getenv('PERSEVERA_DATA_PATH')
        self.AUTOMATION_PATH = os.getenv('PERSEVERA_AUTOMATION_PATH')

        # Load database configuration
        self.DB_CONFIG = {
            'username': os.getenv('PERSEVERA_DB_USER'),
            'password': os.getenv('PERSEVERA_DB_PASSWORD'),
            'host': os.getenv('PERSEVERA_DB_HOST'),
            'port': os.getenv('PERSEVERA_DB_PORT', DEFAULT_CONFIG['DB']['port']),
            'database': os.getenv('PERSEVERA_DB_NAME', DEFAULT_CONFIG['DB']['database'])
        }

        # Load FRED API key
        self.FRED_API_KEY = os.getenv('PERSEVERA_FRED_API_KEY')

        # Load Comdinheiro credentials
        self.COMDINHEIRO_USERNAME = os.getenv('PERSEVERA_COMDINHEIRO_USERNAME')
        self.COMDINHEIRO_PASSWORD = os.getenv('PERSEVERA_COMDINHEIRO_PASSWORD')

        # Load Fibery credentials
        self.FIBERY_API_TOKEN = os.getenv('PERSEVERA_FIBERY_API_TOKEN')
        self.FIBERY_DOMAIN = os.getenv('PERSEVERA_FIBERY_DOMAIN')

        # Load Google Sheets credentials
        self.GS_CLIENT_ID = os.getenv('PERSEVERA_GOOGLESHEETS_CLIENT_ID')
        self.GS_CLIENT_SECRET = os.getenv('PERSEVERA_GOOGLESHEETS_CLIENT_SECRET')
        self.GS_PROJECT_ID = os.getenv('PERSEVERA_GOOGLESHEETS_PROJECT_ID')
        self.GS_API_KEY = os.getenv('PERSEVERA_GOOGLESHEETS_API_KEY')

        # Load logging configuration with environment variable overrides
        self.LOG_CONFIG = DEFAULT_CONFIG['LOG_CONFIG'].copy()

        # Override logging settings from environment variables if provided
        if os.getenv('PERSEVERA_LOG_FORMAT'):
            self.LOG_CONFIG['log_format'] = os.getenv('PERSEVERA_LOG_FORMAT')
        
        if os.getenv('PERSEVERA_LOG_DATE_FORMAT'):
            self.LOG_CONFIG['log_datefmt'] = os.getenv('PERSEVERA_LOG_DATE_FORMAT')
        
        if os.getenv('PERSEVERA_LOG_LEVEL'):
            self.LOG_CONFIG['default_level'] = os.getenv('PERSEVERA_LOG_LEVEL')

        # Load XP Wealth Services (XPWS) configuration
        self.XPWS_TENANT_ID = os.getenv('PERSEVERA_XPWS_TENANT_ID')
        self.XPWS_CLIENT_ID = os.getenv('PERSEVERA_XPWS_CLIENT_ID')
        self.XPWS_CLIENT_SECRET = os.getenv('PERSEVERA_XPWS_CLIENT_SECRET')
        self.XPWS_SCOPE = os.getenv('PERSEVERA_XPWS_SCOPE')
        self.XPWS_BASE_URL = os.getenv('PERSEVERA_XPWS_BASE_URL')
        # Optional service-specific base URLs
        self.XPWS_ASSETS_BASE_URL = os.getenv('PERSEVERA_XPWS_ASSETS_BASE_URL')
        self.XPWS_OPERATIONS_BASE_URL = os.getenv('PERSEVERA_XPWS_OPERATIONS_BASE_URL')
        # Optional overrides/flags
        self.XPWS_TOKEN_URL_OVERRIDE = os.getenv('PERSEVERA_XPWS_TOKEN_URL_OVERRIDE')
        self.XPWS_VERIFY_SSL = os.getenv('PERSEVERA_XPWS_VERIFY_SSL', 'true')
        self.XPWS_USER_AGENT = os.getenv('PERSEVERA_XPWS_USER_AGENT')
        self.XPWS_POSITIONS_V2_USE_LEGACY_PATH = os.getenv(
            'PERSEVERA_XPWS_POSITIONS_V2_USE_LEGACY_PATH', 'false'
        )

        # Load XP Hub Advisor Platform configuration
        self.XPHUB_BEARER_TOKEN = os.getenv('PERSEVERA_XPHUB_BEARER_TOKEN')
        self.XPHUB_SUBSCRIPTION_KEY = os.getenv('PERSEVERA_XPHUB_SUBSCRIPTION_KEY')
        self.XPHUB_CUSTOMER_CODE = os.getenv('PERSEVERA_XPHUB_CUSTOMER_CODE')
        self.XPHUB_BASE_URL = os.getenv('PERSEVERA_XPHUB_BASE_URL')
        self.XPHUB_BRAND = os.getenv('PERSEVERA_XPHUB_BRAND')
        self.XPHUB_SEGMENT_CHANNEL = os.getenv('PERSEVERA_XPHUB_SEGMENT_CHANNEL')
        self.XPHUB_SEGMENT_CODE = os.getenv('PERSEVERA_XPHUB_SEGMENT_CODE')

        # Load IBKR Web API (Trading OAuth 1.0a) configuration
        # See: PERSEVERA_IBKR_* environment variables
        self.IBKR_BASE_URL = os.getenv('PERSEVERA_IBKR_BASE_URL')
        self.IBKR_CONSUMER_KEY = os.getenv('PERSEVERA_IBKR_CONSUMER_KEY')
        self.IBKR_CONSUMER_SECRET = os.getenv('PERSEVERA_IBKR_CONSUMER_SECRET')
        self.IBKR_ACCESS_TOKEN = os.getenv('PERSEVERA_IBKR_ACCESS_TOKEN')
        self.IBKR_ACCESS_TOKEN_SECRET = os.getenv('PERSEVERA_IBKR_ACCESS_TOKEN_SECRET')

        # Load BTG Pactual MFO (Wealth Services / BTGWSProvider) configuration
        self.BTGWS_CLIENT_ID = os.getenv('PERSEVERA_BTGWS_CLIENT_ID')
        self.BTGWS_CLIENT_SECRET = os.getenv('PERSEVERA_BTGWS_CLIENT_SECRET')
        self.BTGWS_AUTH_BASE_URL = os.getenv('PERSEVERA_BTGWS_AUTH_BASE_URL')
        self.BTGWS_ACCOUNTS_BASE_URL = os.getenv('PERSEVERA_BTGWS_ACCOUNTS_BASE_URL')
        self.BTGWS_POSITIONS_BASE_URL = os.getenv('PERSEVERA_BTGWS_POSITIONS_BASE_URL')
        self.BTGWS_TIMEOUT = os.getenv('PERSEVERA_BTGWS_TIMEOUT')
        self.BTGWS_VERIFY_SSL = os.getenv('PERSEVERA_BTGWS_VERIFY_SSL', 'true')
        self.BTGWS_REQUEST_DELAY = os.getenv('PERSEVERA_BTGWS_REQUEST_DELAY')
        self.BTGWS_MAX_RETRIES = os.getenv('PERSEVERA_BTGWS_MAX_RETRIES')
        self.BTGWS_RETRY_BACKOFF = os.getenv('PERSEVERA_BTGWS_RETRY_BACKOFF')

        # ANBIMA Feed API (OAuth2 client_credentials)
        self.ANBIMA_FEED_CLIENT_ID = os.getenv('PERSEVERA_ANBIMA_FEED_CLIENT_ID')
        self.ANBIMA_FEED_CLIENT_SECRET = os.getenv('PERSEVERA_ANBIMA_FEED_CLIENT_SECRET')
        # Default true: cadastro no portal costuma liberar sandbox primeiro; produção exige acesso específico
        self.ANBIMA_FEED_SANDBOX = os.getenv('PERSEVERA_ANBIMA_FEED_SANDBOX', 'true')
        self.ANBIMA_FEED_BASE_URL = os.getenv('PERSEVERA_ANBIMA_FEED_BASE_URL')

        # Warren One (consolidação de carteiras / WarrenOneProvider)
        self.WARRENONE_CLIENT_ID = os.getenv('PERSEVERA_WARRENONE_CLIENT_ID')
        self.WARRENONE_SECRET_KEY = os.getenv('PERSEVERA_WARRENONE_SECRET_KEY')
        self.WARRENONE_TENANT_ID = os.getenv('PERSEVERA_WARRENONE_TENANT_ID')
        self.WARRENONE_ENV = os.getenv('PERSEVERA_WARRENONE_ENV', 'prd')
        self.WARRENONE_AUTH_BASE_URL = os.getenv('PERSEVERA_WARRENONE_AUTH_BASE_URL')
        self.WARRENONE_API_BASE_URL = os.getenv('PERSEVERA_WARRENONE_API_BASE_URL')
        self.WARRENONE_TIMEOUT = os.getenv('PERSEVERA_WARRENONE_TIMEOUT')
        self.WARRENONE_VERIFY_SSL = os.getenv('PERSEVERA_WARRENONE_VERIFY_SSL', 'true')
        self.WARRENONE_REQUEST_DELAY = os.getenv('PERSEVERA_WARRENONE_REQUEST_DELAY')
        self.WARRENONE_MAX_RETRIES = os.getenv('PERSEVERA_WARRENONE_MAX_RETRIES')
        self.WARRENONE_RETRY_BACKOFF = os.getenv('PERSEVERA_WARRENONE_RETRY_BACKOFF')

    def get_gs_client_secret(self):
        return {
            "installed":{
                "client_id":self.GS_CLIENT_ID,
                "project_id":self.GS_PROJECT_ID,
                "auth_uri":"https://accounts.google.com/o/oauth2/auth",
                "token_uri":"https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",
                "client_secret":self.GS_CLIENT_SECRET,
                "redirect_uris":[
                    "http://localhost"
                ]
            }
        }

    def validate(self) -> bool:
        """Validate that all required configuration is present"""
        required_vars = [
            'DATA_PATH',
            'AUTOMATION_PATH',
            'DB_CONFIG.username',
            'DB_CONFIG.password',
            'DB_CONFIG.host'
        ]
        
        for var in required_vars:
            if '.' in var:
                obj, attr = var.split('.')
                if not getattr(self, obj).get(attr):
                    raise ValueError(f"Missing required configuration: {var}")
            elif not getattr(self, var):
                raise ValueError(f"Missing required configuration: {var}")
        
        return True

    def get_db_url(self) -> str:
        """Get database URL while protecting sensitive information"""
        config = self.DB_CONFIG
        return f"postgresql+psycopg2://{config['username']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"

# Create a global settings instance
settings = Settings()