from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    encryption_key:str=''
    session_dir:str='./data/sessions'
    cors_origins:str='http://localhost:3000,https://hub24-message-web-sandy.vercel.app,https://hub24-message-web.vercel.app'
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
