from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .main import app, auth, now_iso
from . import db
from .marketplace import require_admin


def q(table):
    return db.sb.table(table)


class AdminProductUpdate(BaseModel):
    category_id: int | None = None
    title: str | None = None
    description: str | None = None
    price: int | None = None
    stock: int | None = None
    status: str | None = None


@app.get('/v1/admin/market/products')
def admin_market_products(user=Depends(auth)):
    require_admin(user)
    items = q('market_products').select('*').order('created_at', desc=True).limit(1000).execute().data or []
    cats = {str(x['id']): x for x in q('market_categories').select('*').execute().data or []}
    sellers = {str(x['user_id']): x for x in q('seller_profiles').select('*').execute().data or []}
    for item in items:
        item['category'] = cats.get(str(item.get('category_id')))
        item['seller'] = sellers.get(str(item.get('seller_id')))
    return {'items': items, 'count': len(items)}


@app.put('/v1/admin/market/products/{pid}')
def admin_update_market_product(pid: int, p: AdminProductUpdate, user=Depends(auth)):
    require_admin(user)
    rows = q('market_products').select('*').eq('id', pid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, 'PRODUCT_NOT_FOUND')
    old = rows[0]
    payload = {}
    if p.title is not None:
        title = p.title.strip()
        if not title:
            raise HTTPException(400, 'TITLE_REQUIRED')
        payload['title'] = title[:200]
    if p.description is not None:
        payload['description'] = p.description.strip() or None
    if p.category_id is not None:
        cat = q('market_categories').select('id').eq('id', p.category_id).limit(1).execute().data or []
        if not cat:
            raise HTTPException(400, 'CATEGORY_NOT_FOUND')
        payload['category_id'] = p.category_id
    if p.price is not None:
        if p.price < 0:
            raise HTTPException(400, 'INVALID_PRICE')
        payload['price'] = int(p.price)
    if p.stock is not None:
        if p.stock < 0:
            raise HTTPException(400, 'INVALID_STOCK')
        payload['stock'] = int(p.stock)
    if p.status is not None:
        status = p.status.upper()
        if status not in ('ACTIVE', 'PAUSED', 'SOLD_OUT', 'HIDDEN'):
            raise HTTPException(400, 'INVALID_STATUS')
        if status == 'ACTIVE' and not str(old.get('image_url') or '').strip():
            raise HTTPException(409, 'PRODUCT_IMAGE_REQUIRED')
        payload['status'] = status
    if not payload:
        raise HTTPException(400, 'NO_CHANGES')
    payload['updated_at'] = now_iso()
    updated = q('market_products').update(payload).eq('id', pid).execute().data or []
    try:
        q('admin_logs').insert({'admin_user_id': user, 'action': 'MARKET_PRODUCT_UPDATE', 'target_type': 'market_product', 'target_id': str(pid), 'detail': payload}).execute()
    except Exception:
        pass
    return {'ok': True, 'item': updated[0] if updated else None}


@app.delete('/v1/admin/market/products/{pid}')
def admin_delete_market_product(pid: int, user=Depends(auth)):
    require_admin(user)
    rows = q('market_products').select('*').eq('id', pid).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, 'PRODUCT_NOT_FOUND')
    old = rows[0]
    linked = q('escrow_trades').select('id').eq('product_id', pid).limit(1).execute().data or []
    if linked:
        updated = q('market_products').update({'status': 'HIDDEN', 'updated_at': now_iso()}).eq('id', pid).execute().data or []
        try:
            q('admin_logs').insert({'admin_user_id': user, 'action': 'MARKET_PRODUCT_HIDE_LINKED', 'target_type': 'market_product', 'target_id': str(pid), 'detail': {'seller_id': old.get('seller_id')}}).execute()
        except Exception:
            pass
        return {'ok': True, 'deleted': False, 'hidden': True, 'item': updated[0] if updated else None}
    q('market_products').delete().eq('id', pid).execute()
    try:
        q('admin_logs').insert({'admin_user_id': user, 'action': 'MARKET_PRODUCT_DELETE', 'target_type': 'market_product', 'target_id': str(pid), 'detail': {'seller_id': old.get('seller_id'), 'title': old.get('title')}}).execute()
    except Exception:
        pass
    return {'ok': True, 'deleted': True, 'hidden': False}
