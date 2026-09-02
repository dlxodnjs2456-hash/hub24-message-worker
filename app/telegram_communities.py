from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(table):
    return db.sb.table(table)


class CommunityCreate(BaseModel):
    community_name: str
    category: str = '기타'
    description: str = ''
    telegram_url: str
    image_url: str | None = None
    tags: list[str] = []


class CommunityUpdate(BaseModel):
    community_name: str | None = None
    category: str | None = None
    description: str | None = None
    telegram_url: str | None = None
    image_url: str | None = None
    tags: list[str] | None = None
    status: str | None = None


def _clean_tags(tags):
    out = []
    for raw in tags or []:
        t = str(raw or '').strip().lstrip('#')[:24]
        if t and t not in out:
            out.append(t)
        if len(out) >= 8:
            break
    return out


def _validate_link(url):
    u = str(url or '').strip()
    if not (u.startswith('https://t.me/') or u.startswith('http://t.me/') or u.startswith('tg://')):
        raise HTTPException(400, 'TELEGRAM_LINK_REQUIRED')
    return u


@app.get('/v1/telegram-communities')
def list_telegram_communities(category: str = '', search: str = '', user=Depends(auth)):
    x = q('npay_telegram_communities').select('*').eq('status', 'ACTIVE').order('created_at', desc=True)
    if category:
        x = x.eq('category', category)
    if search:
        term = search.strip().replace(',', ' ')
        if term:
            x = x.or_(f'community_name.ilike.%{term}%,description.ilike.%{term}%')
    return {'items': x.limit(300).execute().data or []}


@app.get('/v1/telegram-communities/mine')
def my_telegram_communities(user=Depends(auth)):
    return {'items': q('npay_telegram_communities').select('*').eq('user_id', user).order('created_at', desc=True).execute().data or []}


@app.post('/v1/telegram-communities')
def create_telegram_community(p: CommunityCreate, user=Depends(auth)):
    name = p.community_name.strip()
    if not name:
        raise HTTPException(400, 'COMMUNITY_NAME_REQUIRED')
    payload = {
        'user_id': user,
        'community_name': name[:80],
        'category': (p.category or '기타').strip()[:30] or '기타',
        'description': (p.description or '').strip()[:3000],
        'telegram_url': _validate_link(p.telegram_url),
        'image_url': (p.image_url or '').strip()[:1000] or None,
        'tags': _clean_tags(p.tags),
        'status': 'ACTIVE',
        'updated_at': now_iso(),
    }
    rows = q('npay_telegram_communities').insert(payload).execute().data or []
    return rows[0] if rows else payload


@app.put('/v1/telegram-communities/{cid}')
def update_telegram_community(cid: int, p: CommunityUpdate, user=Depends(auth)):
    rows = q('npay_telegram_communities').select('*').eq('id', cid).eq('user_id', user).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, 'COMMUNITY_NOT_FOUND')
    payload = {}
    if p.community_name is not None:
        name = p.community_name.strip()
        if not name:
            raise HTTPException(400, 'COMMUNITY_NAME_REQUIRED')
        payload['community_name'] = name[:80]
    if p.category is not None:
        payload['category'] = (p.category or '기타').strip()[:30] or '기타'
    if p.description is not None:
        payload['description'] = (p.description or '').strip()[:3000]
    if p.telegram_url is not None:
        payload['telegram_url'] = _validate_link(p.telegram_url)
    if p.image_url is not None:
        payload['image_url'] = (p.image_url or '').strip()[:1000] or None
    if p.tags is not None:
        payload['tags'] = _clean_tags(p.tags)
    if p.status is not None:
        status = p.status.upper()
        if status not in ('ACTIVE', 'HIDDEN'):
            raise HTTPException(400, 'INVALID_STATUS')
        payload['status'] = status
    if not payload:
        raise HTTPException(400, 'NO_CHANGES')
    payload['updated_at'] = now_iso()
    out = q('npay_telegram_communities').update(payload).eq('id', cid).eq('user_id', user).execute().data or []
    return out[0] if out else {'ok': True}


@app.delete('/v1/telegram-communities/{cid}')
def delete_telegram_community(cid: int, user=Depends(auth)):
    rows = q('npay_telegram_communities').select('id').eq('id', cid).eq('user_id', user).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, 'COMMUNITY_NOT_FOUND')
    q('npay_telegram_communities').delete().eq('id', cid).eq('user_id', user).execute()
    return {'ok': True}
