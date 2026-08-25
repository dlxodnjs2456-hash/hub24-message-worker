import re
from fastapi import HTTPException, Query
from .main import app
from . import db

@app.get('/v1/public/referral/validate')
def public_referral_validate(code:str=Query(default='')):
    value=re.sub(r'\s+','',str(code or '')).upper()
    if not re.fullmatch(r'[A-Z0-9]{4,10}',value):
        return {'valid':False}
    rows=db.sb.table('npay_referral_codes').select('user_id').eq('code',value).eq('is_active',True).limit(1).execute().data or []
    return {'valid':bool(rows)}
