import re
from fastapi import HTTPException
from pydantic import BaseModel
from .main import app
from . import db

class UsernameSignup(BaseModel):
    username:str
    password:str

def _clean_username(raw):
    u=str(raw or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9_]{4,20}',u):
        raise HTTPException(400,'아이디는 영문 소문자, 숫자, _ 조합 4~20자로 입력하세요.')
    return u

def synthetic_email(username):
    return f'{username}@users.npay.local'

@app.post('/v1/auth/username-signup')
def username_signup(p:UsernameSignup):
    u=_clean_username(p.username)
    if len(p.password or '')<6:
        raise HTTPException(400,'비밀번호는 6자 이상 입력하세요.')
    email=synthetic_email(u)
    try:
        created=db.sb.auth.admin.create_user({'email':email,'password':p.password,'email_confirm':True,'user_metadata':{'username':u,'login_type':'USERNAME'}})
        user=getattr(created,'user',None)
        if not user or not getattr(user,'id',None):
            raise HTTPException(400,'회원가입에 실패했습니다.')
        return {'ok':True,'username':u}
    except HTTPException:
        raise
    except Exception as e:
        text=str(e)
        if 'already' in text.lower() or 'registered' in text.lower() or 'exists' in text.lower():
            raise HTTPException(409,'이미 사용 중인 아이디입니다.')
        raise HTTPException(400,text[:300])
