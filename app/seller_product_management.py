from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .main import app, auth, now_iso
from . import db


def q(table):
    return db.sb.table(table)


def own_product(pid: int, user: str):
    rows = q('market_products').select('*').eq('id', pid).eq('seller_id', user).limit(1).execute().data or []
    return rows[0] if rows else None


class ProductUpdate(BaseModel):
    category_id: int | None = None
    title: str | None = None
    description: str | None = None
    price: int | None = None
    stock: int | None = None
    status: str | None = None


@app.get('/v1/market/seller/products')
def seller_products(user=Depends(auth)):
    items = q('market_products').select('*').eq('seller_id', user).order('created_at', desc=True).limit(500).execute().data or []
    cats = {str(x['id']): x for x in q('market_categories').select('*').execute().data or []}
    for item in items:
        item['category'] = cats.get(str(item.get('category_id')))
    return {'items': items}


@app.put('/v1/market/seller/products/{pid}')
def update_seller_product(pid: int, p: ProductUpdate, user=Depends(auth)):
    old = own_product(pid, user)
    if not old:
        raise HTTPException(404, 'PRODUCT_NOT_FOUND')
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
    rows = q('market_products').update(payload).eq('id', pid).eq('seller_id', user).execute().data or []
    if not rows:
        raise HTTPException(404, 'PRODUCT_NOT_FOUND')
    return {'ok': True, 'item': rows[0]}


@app.delete('/v1/market/seller/products/{pid}')
def delete_seller_product(pid: int, user=Depends(auth)):
    old = own_product(pid, user)
    if not old:
        raise HTTPException(404, 'PRODUCT_NOT_FOUND')
    linked = q('escrow_trades').select('id').eq('product_id', pid).limit(1).execute().data or []
    if linked:
        rows = q('market_products').update({'status': 'HIDDEN', 'updated_at': now_iso()}).eq('id', pid).eq('seller_id', user).execute().data or []
        return {'ok': True, 'deleted': False, 'hidden': True, 'item': rows[0] if rows else None}
    q('market_products').delete().eq('id', pid).eq('seller_id', user).execute()
    return {'ok': True, 'deleted': True, 'hidden': False}
