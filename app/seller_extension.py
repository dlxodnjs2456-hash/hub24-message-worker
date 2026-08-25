from fastapi import Depends, HTTPException
from pydantic import BaseModel

from . import db
from .main import app, auth, now_iso


class SellerProfileUpdate(BaseModel):
    minimum_price: int


@app.put('/v1/market/seller/profile')
def update_seller_profile(p: SellerProfileUpdate, user=Depends(auth)):
    if p.minimum_price < 0:
        raise HTTPException(400, 'INVALID_MINIMUM_PRICE')
    row = db.sb.table('seller_profiles').select('user_id').eq('user_id', user).limit(1).execute().data or []
    if not row:
        raise HTTPException(404, 'SELLER_PROFILE_NOT_FOUND')
    data = db.sb.table('seller_profiles').update({
        'minimum_price': int(p.minimum_price),
        'updated_at': now_iso(),
    }).eq('user_id', user).execute().data or []
    return {'ok': True, 'item': data[0] if data else None}
