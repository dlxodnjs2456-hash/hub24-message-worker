from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth
from . import db


def q(t): return db.sb.table(t)

def require_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid)
        meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        if meta.get('role')!='admin': raise HTTPException(403,'ADMIN_REQUIRED')
    except HTTPException: raise
    except Exception: raise HTTPException(403,'ADMIN_REQUIRED')

class SupportSettings(BaseModel):
    support_url:str=''

class BroadcastPayload(BaseModel):
    target_type:str='ALL'
    target_user_id:str|None=None
    title:str
    message:str
    link_url:str|None=None

@app.get('/v1/public/site-settings')
def public_site_settings():
    rows=q('market_settings').select('support_url,min_charge_points').eq('id',1).limit(1).execute().data or []
    s=rows[0] if rows else {}
    return {'support_url':s.get('support_url') or '', 'min_charge_points':int(s.get('min_charge_points') or 10000)}

@app.get('/v1/admin/site-settings')
def admin_site_settings(user=Depends(auth)):
    require_admin(user)
    rows=q('market_settings').select('support_url,min_charge_points').eq('id',1).limit(1).execute().data or []
    s=rows[0] if rows else {}
    return {'support_url':s.get('support_url') or '', 'min_charge_points':int(s.get('min_charge_points') or 10000)}

@app.put('/v1/admin/site-settings')
def update_site_settings(p:SupportSettings,user=Depends(auth)):
    require_admin(user)
    url=(p.support_url or '').strip()
    if url and not (url.startswith('https://t.me/') or url.startswith('tg://') or url.startswith('https://telegram.me/')):
        raise HTTPException(400,'TELEGRAM_LINK_REQUIRED')
    q('market_settings').update({'support_url':url}).eq('id',1).execute()
    q('admin_logs').insert({'admin_user_id':user,'action':'SUPPORT_URL_UPDATE','target_type':'site_settings','target_id':'1','detail':{'support_url':url}}).execute()
    return {'ok':True,'support_url':url}

@app.get('/v1/admin/broadcasts')
def admin_broadcasts(user=Depends(auth)):
    require_admin(user)
    rows=q('admin_broadcasts').select('*').order('created_at',desc=True).limit(100).execute().data or []
    return {'items':rows}

@app.post('/v1/admin/broadcasts')
def send_broadcast(p:BroadcastPayload,user=Depends(auth)):
    require_admin(user)
    target=(p.target_type or 'ALL').upper()
    title=(p.title or '').strip(); message=(p.message or '').strip(); link=(p.link_url or '').strip() or None
    if target not in ('ALL','USER'): raise HTTPException(400,'INVALID_TARGET_TYPE')
    if not title or not message: raise HTTPException(400,'TITLE_MESSAGE_REQUIRED')
    if target=='USER' and not p.target_user_id: raise HTTPException(400,'TARGET_USER_REQUIRED')
    targets=[]
    if target=='USER':
        try:
            r=db.sb.auth.admin.get_user_by_id(p.target_user_id)
            if not r or not r.user: raise Exception()
            targets=[str(p.target_user_id)]
        except Exception: raise HTTPException(404,'USER_NOT_FOUND')
    else:
        page=1
        while True:
            res=db.sb.auth.admin.list_users(page=page,per_page=1000)
            users=getattr(res,'users',None) or []
            if not users: break
            targets.extend([str(getattr(u,'id')) for u in users if getattr(u,'id',None)])
            if len(users)<1000: break
            page+=1
    delivered=0
    for uid in targets:
        try:
            q('user_notifications').insert({'user_id':uid,'notification_type':'ADMIN_NOTICE','title':title,'message':message,'link_url':link}).execute(); delivered+=1
        except Exception:
            pass
    row=q('admin_broadcasts').insert({'admin_user_id':user,'target_type':target,'target_user_id':p.target_user_id if target=='USER' else None,'title':title,'message':message,'link_url':link,'delivered_count':delivered}).execute().data
    q('admin_logs').insert({'admin_user_id':user,'action':'ADMIN_BROADCAST_SEND','target_type':'broadcast','target_id':str((row or [{}])[0].get('id') or ''),'detail':{'target_type':target,'target_user_id':p.target_user_id,'delivered_count':delivered,'title':title}}).execute()
    return {'ok':True,'delivered_count':delivered}
