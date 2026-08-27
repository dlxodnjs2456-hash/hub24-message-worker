from datetime import datetime, timezone, timedelta
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .main import app, auth, now_iso
from . import db


def q(table):
    return db.sb.table(table)


def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items():
        x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None


def require_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid)
        meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        if meta.get('role')!='admin':
            raise HTTPException(403,'ADMIN_REQUIRED')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403,'ADMIN_REQUIRED')


def user_exists(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid)
        return bool(r and r.user)
    except Exception:
        return False


def parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except Exception:
        return None


def active_banner(b):
    if not b or not b.get('owner_user_id'):
        return False
    if b.get('is_lifetime'):
        return True
    exp=parse_dt(b.get('expires_at'))
    return bool(exp and exp>datetime.now(timezone.utc))


def banner_slot_count():
    s=one('market_settings',id=1) or {}
    try:
        return max(1,min(6,int(s.get('banner_slot_count') or 3)))
    except Exception:
        return 3


def extend_from(current, days):
    now=datetime.now(timezone.utc)
    dt=parse_dt(current)
    base=dt if dt and dt>now else now
    return base+timedelta(days=days)


def audit(admin_uid, action, target_uid, detail):
    try:
        q('admin_logs').insert({'admin_user_id':admin_uid,'action':action,'target_type':'member','target_id':str(target_uid),'detail':detail}).execute()
    except Exception:
        pass


class VipGrant(BaseModel):
    days:int


class BannerGrant(BaseModel):
    slot:int
    days:int


def validate_days(days):
    if int(days) not in (15,30):
        raise HTTPException(400,'ENTITLEMENT_DAYS_MUST_BE_15_OR_30')
    return int(days)


@app.get('/v1/admin/members/{uid}/entitlements')
def admin_member_entitlements(uid:str,user=Depends(auth)):
    require_admin(user)
    if not user_exists(uid):
        raise HTTPException(404,'USER_NOT_FOUND')
    seller=one('seller_profiles',user_id=uid)
    banners=q('market_banners').select('*').eq('owner_user_id',uid).order('sort_order').execute().data or []
    return {
        'seller': seller,
        'vip_until': seller.get('vip_until') if seller else None,
        'banner_slot_count': banner_slot_count(),
        'banners': [b for b in banners if active_banner(b)],
    }


@app.post('/v1/admin/members/{uid}/grant-vip')
def admin_grant_vip(uid:str,p:VipGrant,user=Depends(auth)):
    require_admin(user)
    days=validate_days(p.days)
    if not user_exists(uid):
        raise HTTPException(404,'USER_NOT_FOUND')
    seller=one('seller_profiles',user_id=uid)
    if not seller:
        raise HTTPException(409,'SELLER_PROFILE_REQUIRED')
    until=extend_from(seller.get('vip_until'),days)
    rows=q('seller_profiles').update({'vip_until':until.isoformat(),'updated_at':now_iso()}).eq('user_id',uid).execute().data or []
    audit(user,'MEMBER_VIP_GRANT',uid,{'days':days,'vip_until':until.isoformat()})
    return {'ok':True,'days':days,'vip_until':until.isoformat(),'seller':rows[0] if rows else seller}


@app.post('/v1/admin/members/{uid}/grant-banner')
def admin_grant_banner(uid:str,p:BannerGrant,user=Depends(auth)):
    require_admin(user)
    days=validate_days(p.days)
    slot=int(p.slot)
    count=banner_slot_count()
    if slot<1 or slot>count:
        raise HTTPException(400,'BANNER_SLOT_NOT_OPEN')
    if not user_exists(uid):
        raise HTTPException(404,'USER_NOT_FOUND')

    existing=one('market_banners',sort_order=slot)
    if existing and active_banner(existing) and str(existing.get('owner_user_id') or '')!=str(uid):
        raise HTTPException(409,'BANNER_SLOT_OCCUPIED')
    if existing and str(existing.get('owner_user_id') or '')==str(uid) and existing.get('is_lifetime') and active_banner(existing):
        raise HTTPException(409,'BANNER_ALREADY_LIFETIME')

    owned=q('market_banners').select('*').eq('owner_user_id',uid).execute().data or []
    active_owned=[b for b in owned if active_banner(b)]
    active_slots={int(b.get('sort_order') or 0) for b in active_owned}
    if slot not in active_slots and len(active_slots)>=2:
        raise HTTPException(409,'BANNER_LIMIT_2')

    current_exp=existing.get('expires_at') if existing and str(existing.get('owner_user_id') or '')==str(uid) else None
    until=extend_from(current_exp,days)
    payload={
        'title': existing.get('title') if existing and str(existing.get('owner_user_id') or '')==str(uid) else f'광고 {slot}',
        'image_url': existing.get('image_url') if existing and str(existing.get('owner_user_id') or '')==str(uid) else '',
        'target_url': existing.get('target_url') if existing and str(existing.get('owner_user_id') or '')==str(uid) else None,
        'sort_order':slot,
        'is_active':True,
        'owner_user_id':uid,
        'purchased_at':now_iso(),
        'expires_at':until.isoformat(),
        'is_lifetime':False,
        'plan_code':f'ADMIN{days}D',
        'purchase_price':0,
        'updated_at':now_iso(),
    }
    if existing:
        rows=q('market_banners').update(payload).eq('id',existing['id']).execute().data or []
        item=rows[0] if rows else existing
    else:
        item=q('market_banners').insert(payload).execute().data[0]
    audit(user,'MEMBER_BANNER_GRANT',uid,{'slot':slot,'days':days,'expires_at':until.isoformat()})
    return {'ok':True,'slot':slot,'days':days,'expires_at':until.isoformat(),'item':item}
