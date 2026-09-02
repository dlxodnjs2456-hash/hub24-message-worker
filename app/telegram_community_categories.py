from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(table): return db.sb.table(table)


def _is_admin(user):
    try:
        r=db.sb.auth.admin.get_user_by_id(user)
        meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        return meta.get('role')=='admin'
    except Exception:
        return False


def _require_admin(user):
    if not _is_admin(user): raise HTTPException(403,'ADMIN_REQUIRED')


class CategoryCreate(BaseModel):
    name:str
    sort_order:int=0
    is_active:bool=True


class CategoryUpdate(BaseModel):
    name:str|None=None
    sort_order:int|None=None
    is_active:bool|None=None


@app.get('/v1/telegram-community-categories')
def public_telegram_community_categories(user=Depends(auth)):
    items=q('npay_telegram_community_categories').select('*').eq('is_active',True).order('sort_order').order('id').execute().data or []
    return {'items':items}


@app.get('/v1/admin/telegram-community-categories')
def admin_telegram_community_categories(user=Depends(auth)):
    _require_admin(user)
    return {'items':q('npay_telegram_community_categories').select('*').order('sort_order').order('id').execute().data or []}


@app.post('/v1/admin/telegram-community-categories')
def admin_create_telegram_community_category(p:CategoryCreate,user=Depends(auth)):
    _require_admin(user)
    name=(p.name or '').strip()[:30]
    if not name: raise HTTPException(400,'CATEGORY_NAME_REQUIRED')
    try:
        rows=q('npay_telegram_community_categories').insert({'name':name,'sort_order':int(p.sort_order or 0),'is_active':bool(p.is_active),'updated_at':now_iso()}).execute().data or []
        return rows[0] if rows else {'ok':True}
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower(): raise HTTPException(409,'CATEGORY_ALREADY_EXISTS')
        raise HTTPException(400,str(e))


@app.put('/v1/admin/telegram-community-categories/{cid}')
def admin_update_telegram_community_category(cid:int,p:CategoryUpdate,user=Depends(auth)):
    _require_admin(user)
    old=q('npay_telegram_community_categories').select('*').eq('id',cid).limit(1).execute().data or []
    if not old: raise HTTPException(404,'CATEGORY_NOT_FOUND')
    payload={}
    if p.name is not None:
        name=(p.name or '').strip()[:30]
        if not name: raise HTTPException(400,'CATEGORY_NAME_REQUIRED')
        payload['name']=name
    if p.sort_order is not None: payload['sort_order']=int(p.sort_order)
    if p.is_active is not None: payload['is_active']=bool(p.is_active)
    if not payload: raise HTTPException(400,'NO_CHANGES')
    payload['updated_at']=now_iso()
    try:
        rows=q('npay_telegram_community_categories').update(payload).eq('id',cid).execute().data or []
        # 카테고리명 변경 시 기존 홍보글 문자열도 함께 맞춰 기존 글이 사라지지 않게 유지한다.
        if 'name' in payload and payload['name'] != old[0].get('name'):
            q('npay_telegram_communities').update({'category':payload['name'],'updated_at':now_iso()}).eq('category',old[0].get('name')).execute()
        return rows[0] if rows else {'ok':True}
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower(): raise HTTPException(409,'CATEGORY_ALREADY_EXISTS')
        raise HTTPException(400,str(e))


@app.delete('/v1/admin/telegram-community-categories/{cid}')
def admin_delete_telegram_community_category(cid:int,user=Depends(auth)):
    _require_admin(user)
    old=q('npay_telegram_community_categories').select('*').eq('id',cid).limit(1).execute().data or []
    if not old: raise HTTPException(404,'CATEGORY_NOT_FOUND')
    name=old[0].get('name')
    # 기존 홍보글은 삭제하지 않고 '기타'로 안전하게 이동한다.
    q('npay_telegram_communities').update({'category':'기타','updated_at':now_iso()}).eq('category',name).execute()
    q('npay_telegram_community_categories').delete().eq('id',cid).execute()
    return {'ok':True,'moved_to':'기타'}
