from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    encryption_key:str=''
    session_dir:str='./data/sessions'
    cors_origins:str='http://localhost:3000,https://hub24-message-web-sandy.vercel.app,https://hub24-message-web.vercel.app'
    supabase_url:str=''
    supabase_service_role_key:str=''
    trongrid_api_key:str=''
    trongrid_base_url:str='https://api.trongrid.io'
    model_config=SettingsConfigDict(env_prefix='HUB24_',env_file='.env',extra='ignore')

settings=Settings()
