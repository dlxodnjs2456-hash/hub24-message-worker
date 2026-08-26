from fastapi import Depends, HTTPException
from pydantic import BaseModel
import re

from . import db
from .main import app, auth, now_iso


class SellerProfileUpdate(BaseModel):
    minimum_price: int | None = None
    telegram_username: str | None = None


def normalize_telegram(v):
    s=(v or '').strip()
    if not s:return None
    s=s[1:] if s.startswith('@') else s
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',s):
        raise HTTPException(400,'INVALID_TELEGRAM_USERNAME')
    return '@'+s


@app.put('/v1/market/seller/profile')
def update_seller_profile(p: SellerProfileUpdate, user=Depends(auth)):
    row = db.sb.table('seller_profiles').select('user_id').eq('user_id', user).limit(1).execute().data or []
    if not row:
        raise HTTPException(404, 'SELLER_PROFILE_NOT_FOUND')
    payload={'updated_at':now_iso()}
    if p.minimum_price is not None:
        if p.minimum_price < 0: raise HTTPException(400, 'INVALID_MINIMUM_PRICE')
        payload['minimum_price']=int(p.minimum_price)
    if p.telegram_username is not None:
        payload['telegram_username']=normalize_telegram(p.telegram_username)
    data = db.sb.table('seller_profiles').update(payload).eq('user_id', user).execute().data or []
    return {'ok': True, 'item': data[0] if data else None}
