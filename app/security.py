from cryptography.fernet import Fernet
from .settings import settings

def _fernet():
    if not settings.encryption_key:
        raise RuntimeError('HUB24_ENCRYPTION_KEY is required')
    return Fernet(settings.encryption_key.encode())

def enc(v:str)->str:
    return _fernet().encrypt(v.encode()).decode()

def dec(v:str)->str:
    return _fernet().decrypt(v.encode()).decode()
