import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    encryption_key:str=''
    session_dir:str='./data/sessions'
    cors_origins:str='http://localhost:3000,https://hub24-message-web-sandy.vercel.app,https://hub24-message-web.vercel.app'
    public_base_url:str='https://hub24-message-worker.onrender.com'
    supabase_url:str=''
    supabase_service_role_key:str=''
    trongrid_api_key:str=''
    trongrid_base_url:str='https://api.trongrid.io'
    check_api_base_url:str=''
    check_api_endpoint:str=''
    check_api_key:str=''
    check_api_secret:str=''
    check_api_auth_type:str='TOKEN'
    check_api_timeout_seconds:int=30
    check_api_rate_limit_per_minute:int=20
    check_api_country:str='KR'
    check_api_filter_type:str='1'
    check_api_poll_interval_seconds:int=5
    check_api_max_wait_seconds:int=900
    check_api_batch_size:int=5000
    model_config=SettingsConfigDict(env_prefix='HUB24_',env_file='.env',extra='ignore')

settings=Settings()

# Backward compatibility with the checker provider env names used by the old bot.
# HUB24_CHECK_* always wins when present.
if not settings.check_api_base_url:
    settings.check_api_base_url=os.getenv('TG_API_BASE_URL','').strip()
if not settings.check_api_endpoint:
    settings.check_api_endpoint=os.getenv('TG_API_ENDPOINT','').strip()
if not settings.check_api_key:
    settings.check_api_key=(os.getenv('TG_API_TOKEN','') or os.getenv('TG_API_KEY','')).strip()
if not settings.check_api_country:
    settings.check_api_country=os.getenv('TG_API_COUNTRY','KR').strip() or 'KR'
if not settings.check_api_filter_type:
    settings.check_api_filter_type=os.getenv('TG_API_FILTER_TYPE','1').strip() or '1'

try:
    settings.check_api_poll_interval_seconds=int(os.getenv('TG_API_POLL_INTERVAL_SECONDS',settings.check_api_poll_interval_seconds))
except Exception:
    pass
try:
    settings.check_api_timeout_seconds=int(os.getenv('TG_API_TIMEOUT_SECONDS',settings.check_api_timeout_seconds))
except Exception:
    pass
try:
    settings.check_api_max_wait_seconds=int(os.getenv('TG_API_MAX_WAIT_SECONDS',settings.check_api_max_wait_seconds))
except Exception:
    pass
try:
    settings.check_api_batch_size=int(os.getenv('API_BATCH_SIZE',settings.check_api_batch_size))
except Exception:
    pass
