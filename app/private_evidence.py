import base64
import re
import uuid
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .main import app, auth
from . import db

BUCKET='dispute-evidence'
MAX_BYTES=5*1024*1024
ALLOWED={'image/jpeg','image/png','image/webp','image/gif'}


def q(table): return db.sb.table(table)

def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None

def require_participant(tid,user):
    t=one('escrow_trades',id=tid)
    if not t or str(user) not in (str(t['buyer_id']),str(t['seller_id'])):
        raise HTTPException(403,'TRADE_ACCESS_DENIED')
    return t

def require_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid)
        meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        if meta.get('role')!='admin': raise HTTPException(403,'ADMIN_REQUIRED')
    except HTTPException: raise
    except Exception: raise HTTPException(403,'ADMIN_REQUIRED')

def signed(path):
    if not path: return None
    if str(path).startswith('http://') or str(path).startswith('https://'): return path
    p=str(path).removeprefix('private:')
    try:
        r=db.sb.storage.from_(BUCKET).create_signed_url(p,3600)
        if isinstance(r,dict): return r.get('signedURL') or r.get('signedUrl') or r.get('signed_url')
    except Exception:
        return None
    return None

def decorate(rows):
    out=[]
    for row in rows or []:
        x=dict(row)
        raw=x.get('file_url')
        if raw and not str(raw).startswith('http'):
            x['file_url']=signed(raw)
        out.append(x)
    return out

class EvidenceCreatePrivate(BaseModel):
    evidence_type:str='TEXT'
    content:str|None=None
    file_base64:str|None=None
    file_name:str|None=None
    content_type:str|None=None

@app.get('/v1/market/trades/{tid}/evidence')
def evidence_list_private(tid:int,user=Depends(auth)):
    require_participant(tid,user)
    rows=q('trade_dispute_evidence').select('*').eq('trade_id',tid).order('created_at').execute().data or []
    return {'items':decorate(rows)}

@app.post('/v1/market/trades/{tid}/evidence')
def evidence_add_private(tid:int,p:EvidenceCreatePrivate,user=Depends(auth)):
    t=require_participant(tid,user)
    if t.get('status') not in ('DISPUTED','CANCEL_REQUESTED','SELLER_COMPLETED','ESCROWED','ACCEPTED'):
        raise HTTPException(409,'EVIDENCE_NOT_ALLOWED')
    content=(p.content or '').strip() or None
    stored=None
    if p.file_base64:
        ct=(p.content_type or '').lower().strip()
        if ct not in ALLOWED: raise HTTPException(400,'EVIDENCE_IMAGE_TYPE_NOT_ALLOWED')
        try:
            raw=base64.b64decode(p.file_base64,validate=True)
        except Exception:
            raise HTTPException(400,'INVALID_EVIDENCE_IMAGE')
        if not raw or len(raw)>MAX_BYTES: raise HTTPException(400,'EVIDENCE_IMAGE_MAX_5MB')
        ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif'}[ct]
        safe=re.sub(r'[^a-zA-Z0-9._-]','_',p.file_name or '')[:60]
        path=f'trade-{tid}/{user}/{uuid.uuid4().hex}-{safe or "evidence"}.{ext}'
        try:
            db.sb.storage.from_(BUCKET).upload(path,raw,{'content-type':ct,'upsert':'false'})
            stored='private:'+path
        except Exception as e:
            raise HTTPException(500,'EVIDENCE_UPLOAD_FAILED') from e
    if not content and not stored: raise HTTPException(400,'EVIDENCE_REQUIRED')
    rows=q('trade_dispute_evidence').insert({'trade_id':tid,'user_id':user,'evidence_type':'IMAGE' if stored else 'TEXT','content':content,'file_url':stored}).execute().data or []
    return decorate(rows)[0] if rows else {'ok':True}

@app.get('/v1/admin/market/trades/{tid}/evidence')
def admin_evidence_private(tid:int,user=Depends(auth)):
    require_admin(user)
    rows=q('trade_dispute_evidence').select('*').eq('trade_id',tid).order('created_at').execute().data or []
    return {'items':decorate(rows)}
