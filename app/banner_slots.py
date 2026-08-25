from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(table):
    return db.sb.table(table)


def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None


def is_admin(user):
    try:
        r=db.sb.auth.admin.get_user_by_id(user)
        meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        return meta.get('role')=='admin'
    except Exception:
        return False


def active(b):
    if not b or not b.get('owner_user_id'): return False
    if b.get('is_lifetime'): return True
    exp=b.get('expires_at')
    if not exp: return False
    try:
        return datetime.fromisoformat(str(exp).replace('Z','+00:00')) > datetime.now(timezone.utc)
    except Exception:
        return False


class BannerPurchase(BaseModel):
    plan: str


class BannerCreative(BaseModel):
    title: str | None = None
    image_url: str
    target_url: str | None = None


@app.get('/v1/market/banner-slots')
def banner_slots(user=Depends(auth)):
    rows=q('market_banners').select('*').gte('sort_order',1).lte('sort_order',6).order('sort_order').execute().data or []
    by={int(x['sort_order']):x for x in rows}
    items=[]
    for slot in range(1,7):
        b=by.get(slot)
        occupied=active(b)
        admin_banner=bool(b and not b.get('owner_user_id') and b.get('is_active') and b.get('image_url'))
        items.append({
            'slot':slot,
            'id':b.get('id') if b else None,
            'title':b.get('title') if b else None,
            'image_url':b.get('image_url') if b and (occupied or admin_banner) else '',
            'target_url':b.get('target_url') if b and (occupied or admin_banner) else None,
            'is_active':bool(b and b.get('is_active') and (occupied or admin_banner)),
            'available':not occupied,
            'owned_by_me':bool(b and str(b.get('owner_user_id') or '')==str(user) and occupied),
            'expires_at':b.get('expires_at') if occupied else None,
            'is_lifetime':bool(b and b.get('is_lifetime') and occupied),
            'plan_code':b.get('plan_code') if occupied else None,
        })
    return {'items':items,'plans':[{'code':'1M','label':'1개월','price':1000000},{'code':'3M','label':'3개월','price':2700000},{'code':'6M','label':'6개월','price':5000000},{'code':'LIFETIME','label':'서비스 종료기한 없음','price':10000000}], 'max_slots_per_user':2, 'image_guide':'권장 1200×360px (10:3), JPG/PNG/WebP, 최대 5MB'}


@app.post('/v1/market/banner-slots/{slot}/purchase')
def purchase_banner(slot:int,p:BannerPurchase,user=Depends(auth)):
    try:
        result=db.sb.rpc('npay_purchase_banner_slot',{'p_user':user,'p_slot':slot,'p_plan':p.plan.upper()}).execute().data
        return {'ok':True,'purchase':result}
    except Exception as e:
        m=str(e)
        for code in ['INVALID_SLOT','INVALID_PLAN','INSUFFICIENT_POINT','SLOT_UNAVAILABLE','BANNER_LIMIT_2']:
            if code in m: raise HTTPException(400,code)
        raise HTTPException(400,m[:500])


@app.put('/v1/market/banner-slots/{slot}/creative')
def update_banner_creative(slot:int,p:BannerCreative,user=Depends(auth)):
    b=one('market_banners',sort_order=slot)
    if not b: raise HTTPException(404,'BANNER_SLOT_NOT_FOUND')
    own=str(b.get('owner_user_id') or '')==str(user) and active(b)
    if not own and not is_admin(user): raise HTTPException(403,'BANNER_EDIT_FORBIDDEN')
    if not p.image_url or not p.image_url.startswith('http'): raise HTTPException(400,'BANNER_IMAGE_REQUIRED')
    payload={'image_url':p.image_url,'target_url':p.target_url,'is_active':True,'updated_at':now_iso()}
    if p.title is not None: payload['title']=p.title.strip() or f'광고 {slot}'
    rows=q('market_banners').update(payload).eq('id',b['id']).execute().data or []
    return rows[0] if rows else {'ok':True}
