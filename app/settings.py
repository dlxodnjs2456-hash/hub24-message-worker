from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    encryption_key:str=''
    session_dir:str='./data/sessions'
    cors_origins:str='http://localhost:3000'
    supabase_url:str=''
    supabase_service_role_key:str=''
    model_config=SettingsConfigDict(env_prefix='HUB24_',env_file='.env',extra='ignore')

settings=Settings()
