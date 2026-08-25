from datetime import datetime, timezone, timedelta
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


def user_info(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid)
        u=r.user if r else None
        meta=(getattr(u,'app_metadata',None) or {}) if u else {}
        email=getattr(u,'email',None) or ''
        name=(getattr(u,'user_metadata',None) or {}).get('nickname') if u else None
        return {'role':meta.get('role'),'name':name or (email.split('@')[0] if email else '회원')}
    except Exception:
        return {'role':None,'name':'회원'}


def board_admin(uid):
    return user_info(uid).get('role') in ('admin','board_admin')


def parse_dt(value):
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except Exception: return None


def enforce_cooldown(uid, board_type, hours):
    rows=q('community_posts').select('created_at').eq('user_id',uid).eq('board_type',board_type).order('created_at',desc=True).limit(1).execute().data or []
    if not rows: return
    last=parse_dt(rows[0].get('created_at'))
    if not last: return
    now=datetime.now(timezone.utc)
    wait=timedelta(hours=hours)-(now-last)
    if wait.total_seconds()>0:
        mins=max(1,int((wait.total_seconds()+59)//60))
        raise HTTPException(429,f'POST_COOLDOWN:{mins}')


class PostCreate(BaseModel):
    board_type:str
    title:str
    content:str

class CommentCreate(BaseModel):
    content:str

class PinUpdate(BaseModel):
    is_pinned: bool


@app.get('/v1/community/posts')
def community_posts(board_type:str='FREE', user=Depends(auth)):
    board=board_type.upper()
    if board not in ('FREE','JOBS','BLACKLIST'): raise HTTPException(400,'INVALID_BOARD')
    rows=q('community_posts').select('*').eq('board_type',board).eq('is_hidden',False).order('is_pinned',desc=True).order('created_at',desc=True).limit(300).execute().data or []
    return {'items':rows}


@app.post('/v1/community/posts')
def create_community_post(p:PostCreate,user=Depends(auth)):
    board=p.board_type.upper()
    if board not in ('FREE','JOBS','BLACKLIST'): raise HTTPException(400,'INVALID_BOARD')
    if not p.title.strip() or not p.content.strip(): raise HTTPException(400,'TITLE_CONTENT_REQUIRED')
    if board=='BLACKLIST' and not board_admin(user): raise HTTPException(403,'BOARD_ADMIN_ONLY')
    if board=='FREE': enforce_cooldown(user,'FREE',1)
    if board=='JOBS': enforce_cooldown(user,'JOBS',24)
    info=user_info(user)
    row=q('community_posts').insert({'board_type':board,'user_id':user,'author_name':info['name'],'title':p.title.strip(),'content':p.content.strip()}).execute().data[0]
    return row


@app.get('/v1/community/posts/{pid}')
def community_post(pid:int,user=Depends(auth)):
    post=one('community_posts',id=pid)
    if not post or post.get('is_hidden'): raise HTTPException(404,'POST_NOT_FOUND')
    try: q('community_posts').update({'view_count':int(post.get('view_count') or 0)+1}).eq('id',pid).execute()
    except Exception: pass
    comments=q('community_comments').select('*').eq('post_id',pid).eq('is_hidden',False).order('created_at').limit(500).execute().data or []
    return {'item':post,'comments':comments,'can_write_blacklist':board_admin(user),'board_admin':board_admin(user)}


@app.post('/v1/community/posts/{pid}/comments')
def add_community_comment(pid:int,p:CommentCreate,user=Depends(auth)):
    if not p.content.strip(): raise HTTPException(400,'COMMENT_REQUIRED')
    post=one('community_posts',id=pid)
    if not post or post.get('is_hidden'): raise HTTPException(404,'POST_NOT_FOUND')
    info=user_info(user)
    row=q('community_comments').insert({'post_id':pid,'user_id':user,'author_name':info['name'],'content':p.content.strip()}).execute().data[0]
    q('community_posts').update({'comment_count':int(post.get('comment_count') or 0)+1,'updated_at':now_iso()}).eq('id',pid).execute()
    return row


@app.put('/v1/community/posts/{pid}/pin')
def pin_community_post(pid:int,p:PinUpdate,user=Depends(auth)):
    if not board_admin(user): raise HTTPException(403,'BOARD_ADMIN_ONLY')
    post=one('community_posts',id=pid)
    if not post or post.get('is_hidden'): raise HTTPException(404,'POST_NOT_FOUND')
    rows=q('community_posts').update({'is_pinned':bool(p.is_pinned),'updated_at':now_iso()}).eq('id',pid).execute().data or []
    return rows[0] if rows else {'ok':True,'is_pinned':bool(p.is_pinned)}


@app.get('/v1/community/permissions')
def community_permissions(user=Depends(auth)):
    return {'board_admin':board_admin(user)}
