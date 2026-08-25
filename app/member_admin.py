from fastapi import Depends, HTTPException, Query
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

class PointAdjust(BaseModel):
    amount:int
    memo:str

def wallet_for(uid):
    rows=q('point_wallets').select('*').eq('user_id',uid).limit(1).execute().data or []
    return rows[0] if rows else {'available_balance':0,'escrow_balance':0,'settlement_balance':0}

@app.get('/v1/admin/members')
def admin_members(search:str=Query(default=''), user=Depends(auth)):
    require_admin(user)
    try:
        res=db.sb.auth.admin.list_users(page=1,per_page=1000)
        users=getattr(res,'users',None) or (res if isinstance(res,list) else [])
    except Exception as e:
        raise HTTPException(500,str(e)[:300])
    needle=search.strip().lower(); items=[]
    for u in users:
        email=str(getattr(u,'email',None) or '')
        uid=str(getattr(u,'id',None) or '')
        meta=getattr(u,'user_metadata',None) or {}; appmeta=getattr(u,'app_metadata',None) or {}
        if needle and needle not in email.lower() and needle not in uid.lower() and needle not in str(meta.get('nickname') or meta.get('name') or '').lower():
            continue
        w=wallet_for(uid)
        ref=q('npay_referral_codes').select('code').eq('user_id',uid).limit(1).execute().data or []
        items.append({'id':uid,'email':email,'created_at':str(getattr(u,'created_at',None) or ''),'last_sign_in_at':str(getattr(u,'last_sign_in_at',None) or ''),'role':appmeta.get('role') or 'user','name':meta.get('nickname') or meta.get('name') or '','referral_code':ref[0]['code'] if ref else None,'wallet':w})
    return {'items':items[:500],'count':len(items[:500])}

@app.get('/v1/admin/members/{uid}')
def admin_member(uid:str,user=Depends(auth)):
    require_admin(user)
    try:r=db.sb.auth.admin.get_user_by_id(uid);u=r.user
    except Exception: raise HTTPException(404,'USER_NOT_FOUND')
    ledger=q('point_ledger').select('*').eq('user_id',uid).order('created_at',desc=True).limit(100).execute().data or []
    charges=q('npay_usdt_charge_requests').select('*').eq('user_id',uid).order('created_at',desc=True).limit(50).execute().data or []
    return {'id':uid,'email':getattr(u,'email',None),'created_at':str(getattr(u,'created_at',None) or ''),'last_sign_in_at':str(getattr(u,'last_sign_in_at',None) or ''),'wallet':wallet_for(uid),'ledger':ledger,'usdt_charge_requests':charges}

@app.post('/v1/admin/members/{uid}/points')
def admin_adjust_member_points(uid:str,p:PointAdjust,user=Depends(auth)):
    require_admin(user)
    if p.amount==0: raise HTTPException(400,'AMOUNT_ZERO')
    if not p.memo.strip(): raise HTTPException(400,'MEMO_REQUIRED')
    try:
        new_balance=db.sb.rpc('npay_admin_adjust_points',{'p_user':uid,'p_amount':p.amount,'p_memo':p.memo.strip()}).execute().data
        q('admin_logs').insert({'admin_user_id':user,'action':'MEMBER_POINT_ADJUST','target_type':'member','target_id':uid,'detail':{'amount':p.amount,'memo':p.memo.strip(),'new_balance':new_balance}}).execute()
        return {'ok':True,'new_balance':new_balance}
    except Exception as e:
        msg=str(e)
        if 'INSUFFICIENT_AVAILABLE_BALANCE' in msg: raise HTTPException(409,'INSUFFICIENT_AVAILABLE_BALANCE')
        raise HTTPException(400,msg[:300])
