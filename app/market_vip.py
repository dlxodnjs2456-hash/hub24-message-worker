from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(table):
    return db.sb.table(table)


def one(table, **eq):
    x = q(table).select('*')
    for k, v in eq.items():
        x = x.eq(k, v)
    rows = x.limit(1).execute().data or []
    return rows[0] if rows else None


def require_admin(user):
    try:
        r = db.sb.auth.admin.get_user_by_id(user)
        meta = (getattr(r.user, 'app_metadata', None) or {}) if r and r.user else {}
        if meta.get('role') != 'admin':
            raise HTTPException(403, 'ADMIN_REQUIRED')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, 'ADMIN_REQUIRED')


def validate_banner_slot(slot: int):
    if slot < 1 or slot > 6:
        raise HTTPException(400, 'BANNER_SLOT_MUST_BE_1_TO_6')


class ProductCreateV2(BaseModel):
    category_id: int | None = None
    title: str
    description: str | None = None
    price: int
    stock: int | None = None
    image_url: str


class BannerCreate(BaseModel):
    title: str
    image_url: str
    target_url: str | None = None
    sort_order: int = 1
    is_active: bool = True


class BannerUpdate(BaseModel):
    title: str | None = None
    image_url: str | None = None
    target_url: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class VipSettings(BaseModel):
    vip_price: int
    vip_days: int


@app.get('/v1/market/banners')
def market_banners(user=Depends(auth)):
    rows = q('market_banners').select('*').eq('is_active', True).gte('sort_order', 1).lte('sort_order', 6).order('sort_order').execute().data or []
    return {'items': rows}


@app.post('/v1/market/products-v2')
def create_product_v2(p: ProductCreateV2, user=Depends(auth)):
    seller = one('seller_profiles', user_id=user)
    if not seller or seller.get('status') != 'APPROVED':
        raise HTTPException(403, 'APPROVED_SELLER_REQUIRED')
    if p.price < 0:
        raise HTTPException(400, 'INVALID_PRICE')
    if not p.image_url or not p.image_url.startswith('http'):
        raise HTTPException(400, 'PRODUCT_IMAGE_REQUIRED')
    if not p.title.strip():
        raise HTTPException(400, 'PRODUCT_TITLE_REQUIRED')
    payload = {
        'seller_id': user,
        'category_id': p.category_id,
        'title': p.title.strip(),
        'description': p.description,
        'price': p.price,
        'stock': p.stock,
        'image_url': p.image_url,
        'status': 'ACTIVE',
    }
    try:
        return q('market_products').insert(payload).execute().data[0]
    except Exception as e:
        if 'SELLER_POST_COOLDOWN_24H' in str(e):
            raise HTTPException(429, 'SELLER_POST_COOLDOWN_24H')
        raise HTTPException(400, str(e))


@app.post('/v1/market/seller/vip')
def purchase_vip(user=Depends(auth)):
    try:
        until = db.sb.rpc('hub24_purchase_vip_seller', {'p_user': user}).execute().data
        settings = one('market_settings', id=1) or {}
        return {'ok': True, 'vip_until': until, 'vip_price': int(settings.get('vip_price') or 150000), 'vip_days': int(settings.get('vip_days') or 30)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get('/v1/market/vip-info')
def vip_info(user=Depends(auth)):
    settings = one('market_settings', id=1) or {}
    seller = one('seller_profiles', user_id=user)
    return {'price': int(settings.get('vip_price') or 150000), 'days': int(settings.get('vip_days') or 30), 'vip_until': seller.get('vip_until') if seller else None}


@app.get('/v1/admin/market/banners')
def admin_banners(user=Depends(auth)):
    require_admin(user)
    return {'items': q('market_banners').select('*').order('sort_order').order('id').execute().data or []}


@app.post('/v1/admin/market/banners')
def admin_add_banner(p: BannerCreate, user=Depends(auth)):
    require_admin(user)
    validate_banner_slot(p.sort_order)
    payload = p.model_dump()
    payload['updated_at'] = now_iso()
    existing = one('market_banners', sort_order=p.sort_order)
    if existing:
        rows = q('market_banners').update(payload).eq('id', existing['id']).execute().data or []
        return rows[0] if rows else existing
    return q('market_banners').insert(payload).execute().data[0]


@app.put('/v1/admin/market/banners/{bid}')
def admin_update_banner(bid: int, p: BannerUpdate, user=Depends(auth)):
    require_admin(user)
    if p.sort_order is not None:
        validate_banner_slot(p.sort_order)
        collision = one('market_banners', sort_order=p.sort_order)
        if collision and int(collision['id']) != bid:
            raise HTTPException(409, 'BANNER_SLOT_ALREADY_USED')
    payload = {k: v for k, v in p.model_dump().items() if v is not None}
    payload['updated_at'] = now_iso()
    rows = q('market_banners').update(payload).eq('id', bid).execute().data or []
    if not rows:
        raise HTTPException(404, 'BANNER_NOT_FOUND')
    return rows[0]


@app.delete('/v1/admin/market/banners/{bid}')
def admin_delete_banner(bid: int, user=Depends(auth)):
    require_admin(user)
    q('market_banners').delete().eq('id', bid).execute()
    return {'ok': True}


@app.put('/v1/admin/market/vip-settings')
def admin_vip_settings(p: VipSettings, user=Depends(auth)):
    require_admin(user)
    if p.vip_price < 0 or p.vip_days <= 0:
        raise HTTPException(400, 'INVALID_VIP_SETTINGS')
    rows = q('market_settings').update({'vip_price': p.vip_price, 'vip_days': p.vip_days, 'updated_at': now_iso()}).eq('id', 1).execute().data or []
    return rows[0] if rows else {'vip_price': p.vip_price, 'vip_days': p.vip_days}
