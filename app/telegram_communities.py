import math
from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(table): return db.sb.table(table)

class CommunityCreate(BaseModel):
    community_name:str; category:str='기타'; description:str=''; telegram_url:str; image_url:str|None=None; tags:list[str]=[]
class CommunityUpdate(BaseModel):
    community_name:str|None=None; category:str|None=None; description:str|None=None; telegram_url:str|None=None; image_url:str|None=None; tags:list[str]|None=None; status:str|None=None
class CommentCreate(BaseModel): body:str

def _clean_tags(tags):
    out=[]
    for raw in tags or []:
        t=str(raw or '').strip().lstrip('#')[:24]
        if t and t not in out: out.append(t)
        if len(out)>=8: break
    return out

def _validate_link(url):
    u=str(url or '').strip()
    if not (u.startswith('https://t.me/') or u.startswith('http://t.me/') or u.startswith('tg://')): raise HTTPException(400,'TELEGRAM_LINK_REQUIRED')
    return u

def _username(user):
    try:
        r=db.sb.auth.admin.get_user_by_id(user); meta=(getattr(r.user,'user_metadata',None) or {}) if r and r.user else {}
        return str(meta.get('username') or 'user')[:24]
    except Exception:return 'user'

def _score(x):
    likes=int(x.get('like_count') or 0); comments=int(x.get('comment_count') or 0); views=int(x.get('view_count') or 0)
    activity=x.get('last_activity_at') or x.get('created_at')
    age_days=30.0
    try:
        dt=datetime.fromisoformat(str(activity).replace('Z','+00:00')); age_days=max(0,(datetime.now(timezone.utc)-dt).total_seconds()/86400)
    except Exception: pass
    freshness=max(0,18-age_days*.6)
    return round(likes*6 + comments*4 + math.log10(views+1)*8 + freshness,3)

def _decorate(items,user):
    liked={int(x['community_id']) for x in (q('npay_telegram_community_likes').select('community_id').eq('user_id',user).execute().data or [])}
    for x in items:
        x['rank_score']=_score(x); x['liked_by_me']=int(x['id']) in liked
    items.sort(key=lambda z:(z['rank_score'],z.get('last_activity_at') or z.get('created_at') or ''),reverse=True)
    for i,x in enumerate(items,1): x['rank']=i
    return items

@app.get('/v1/telegram-communities')
def list_telegram_communities(category:str='',search:str='',user=Depends(auth)):
    x=q('npay_telegram_communities').select('*').eq('status','ACTIVE')
    if category:x=x.eq('category',category)
    if search:
        term=search.strip().replace(',',' ')
        if term:x=x.or_(f'community_name.ilike.%{term}%,description.ilike.%{term}%')
    return {'items':_decorate(x.limit(300).execute().data or [],user)}

@app.get('/v1/telegram-communities/mine')
def my_telegram_communities(user=Depends(auth)):
    return {'items':q('npay_telegram_communities').select('*').eq('user_id',user).order('created_at',desc=True).execute().data or []}

@app.post('/v1/telegram-communities')
def create_telegram_community(p:CommunityCreate,user=Depends(auth)):
    name=p.community_name.strip()
    if not name:raise HTTPException(400,'COMMUNITY_NAME_REQUIRED')
    payload={'user_id':user,'community_name':name[:80],'category':(p.category or '기타').strip()[:30] or '기타','description':(p.description or '').strip()[:3000],'telegram_url':_validate_link(p.telegram_url),'image_url':(p.image_url or '').strip()[:1000] or None,'tags':_clean_tags(p.tags),'status':'ACTIVE','updated_at':now_iso(),'last_activity_at':now_iso()}
    rows=q('npay_telegram_communities').insert(payload).execute().data or [];return rows[0] if rows else payload

@app.post('/v1/telegram-communities/{cid}/view')
def view_community(cid:int,user=Depends(auth)):
    rows=q('npay_telegram_communities').select('id,view_count').eq('id',cid).eq('status','ACTIVE').limit(1).execute().data or []
    if not rows:raise HTTPException(404,'COMMUNITY_NOT_FOUND')
    count=int(rows[0].get('view_count') or 0)+1
    q('npay_telegram_communities').update({'view_count':count,'last_activity_at':now_iso()}).eq('id',cid).execute();return {'ok':True,'view_count':count}

@app.post('/v1/telegram-communities/{cid}/like')
def toggle_like(cid:int,user=Depends(auth)):
    c=q('npay_telegram_communities').select('id,like_count').eq('id',cid).eq('status','ACTIVE').limit(1).execute().data or []
    if not c:raise HTTPException(404,'COMMUNITY_NOT_FOUND')
    old=q('npay_telegram_community_likes').select('id').eq('community_id',cid).eq('user_id',user).limit(1).execute().data or []
    if old:
        q('npay_telegram_community_likes').delete().eq('community_id',cid).eq('user_id',user).execute();liked=False
    else:
        q('npay_telegram_community_likes').insert({'community_id':cid,'user_id':user}).execute();liked=True
    count=len(q('npay_telegram_community_likes').select('id').eq('community_id',cid).execute().data or [])
    q('npay_telegram_communities').update({'like_count':count,'last_activity_at':now_iso()}).eq('id',cid).execute();return {'ok':True,'liked':liked,'like_count':count}

@app.get('/v1/telegram-communities/{cid}/comments')
def community_comments(cid:int,user=Depends(auth)):
    return {'items':q('npay_telegram_community_comments').select('*').eq('community_id',cid).eq('status','ACTIVE').order('created_at').limit(300).execute().data or []}

@app.post('/v1/telegram-communities/{cid}/comments')
def add_comment(cid:int,p:CommentCreate,user=Depends(auth)):
    body=(p.body or '').strip()
    if not body:raise HTTPException(400,'댓글을 입력하세요.')
    if len(body)>1000:raise HTTPException(400,'댓글은 1000자 이하로 입력하세요.')
    c=q('npay_telegram_communities').select('id').eq('id',cid).eq('status','ACTIVE').limit(1).execute().data or []
    if not c:raise HTTPException(404,'COMMUNITY_NOT_FOUND')
    row=q('npay_telegram_community_comments').insert({'community_id':cid,'user_id':user,'username':_username(user),'body':body}).execute().data[0]
    count=len(q('npay_telegram_community_comments').select('id').eq('community_id',cid).eq('status','ACTIVE').execute().data or [])
    q('npay_telegram_communities').update({'comment_count':count,'last_activity_at':now_iso()}).eq('id',cid).execute();return row

@app.put('/v1/telegram-communities/{cid}')
def update_telegram_community(cid:int,p:CommunityUpdate,user=Depends(auth)):
    rows=q('npay_telegram_communities').select('*').eq('id',cid).eq('user_id',user).limit(1).execute().data or []
    if not rows:raise HTTPException(404,'COMMUNITY_NOT_FOUND')
    payload={}
    if p.community_name is not None:
        name=p.community_name.strip()
        if not name:raise HTTPException(400,'COMMUNITY_NAME_REQUIRED')
        payload['community_name']=name[:80]
    if p.category is not None:payload['category']=(p.category or '기타').strip()[:30] or '기타'
    if p.description is not None:payload['description']=(p.description or '').strip()[:3000]
    if p.telegram_url is not None:payload['telegram_url']=_validate_link(p.telegram_url)
    if p.image_url is not None:payload['image_url']=(p.image_url or '').strip()[:1000] or None
    if p.tags is not None:payload['tags']=_clean_tags(p.tags)
    if p.status is not None:
        status=p.status.upper()
        if status not in ('ACTIVE','HIDDEN'):raise HTTPException(400,'INVALID_STATUS')
        payload['status']=status
    if not payload:raise HTTPException(400,'NO_CHANGES')
    payload['updated_at']=now_iso();payload['last_activity_at']=now_iso()
    out=q('npay_telegram_communities').update(payload).eq('id',cid).eq('user_id',user).execute().data or [];return out[0] if out else {'ok':True}

@app.delete('/v1/telegram-communities/{cid}')
def delete_telegram_community(cid:int,user=Depends(auth)):
    rows=q('npay_telegram_communities').select('id').eq('id',cid).eq('user_id',user).limit(1).execute().data or []
    if not rows:raise HTTPException(404,'COMMUNITY_NOT_FOUND')
    q('npay_telegram_communities').delete().eq('id',cid).eq('user_id',user).execute();return {'ok':True}
