from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(table): return db.sb.table(table)
def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None
def is_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid);meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {};return meta.get('role')=='admin'
    except Exception:return False
def require_admin(uid):
    if not is_admin(uid):raise HTTPException(403,'ADMIN_REQUIRED')
def audit(uid,action,target_type=None,target_id=None,detail=None):
    try:q('admin_logs').insert({'admin_user_id':uid,'action':action,'target_type':target_type,'target_id':str(target_id) if target_id is not None else None,'detail':detail or {}}).execute()
    except Exception:pass

class EvidenceCreate(BaseModel): evidence_type:str='TEXT';content:str|None=None;file_url:str|None=None
class WithdrawalCreateV2(BaseModel): amount:int;payout_method:str;payout_details:str;user_note:str|None=None
class WithdrawalResolveV2(BaseModel): action:str;admin_reference:str|None=None;admin_note:str|None=None
class ReferralSettings(BaseModel): referral_qualification_charge:int;referral_monthly_reward_cap:int

@app.get('/v1/notifications')
def notifications(user=Depends(auth)):
    items=q('user_notifications').select('*').eq('user_id',user).order('created_at',desc=True).limit(100).execute().data or [];return {'items':items,'unread_count':sum(1 for x in items if not x.get('is_read'))}
@app.post('/v1/notifications/{nid}/read')
def notification_read(nid:int,user=Depends(auth)):
    q('user_notifications').update({'is_read':True}).eq('id',nid).eq('user_id',user).execute();return {'ok':True}
@app.post('/v1/notifications/read-all')
def notifications_read_all(user=Depends(auth)):
    q('user_notifications').update({'is_read':True}).eq('user_id',user).eq('is_read',False).execute();return {'ok':True}

@app.post('/v1/wallet/withdrawals-v2')
def withdrawal_v2(p:WithdrawalCreateV2,user=Depends(auth)):
    if p.amount<=0:raise HTTPException(400,'INVALID_AMOUNT')
    if not p.payout_method.strip() or not p.payout_details.strip():raise HTTPException(400,'PAYOUT_INFO_REQUIRED')
    try:return {'ok':True,'id':db.sb.rpc('hub24_request_withdrawal_v2',{'p_user':user,'p_amount':p.amount,'p_method':p.payout_method,'p_details':p.payout_details,'p_note':p.user_note}).execute().data}
    except Exception as e:raise HTTPException(400,str(e))

@app.get('/v1/market/trades/{tid}/evidence')
def trade_evidence(tid:int,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])):raise HTTPException(403,'TRADE_ACCESS_DENIED')
    return {'items':q('trade_dispute_evidence').select('*').eq('trade_id',tid).order('created_at').execute().data or []}
@app.post('/v1/market/trades/{tid}/evidence')
def add_trade_evidence(tid:int,p:EvidenceCreate,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])):raise HTTPException(403,'TRADE_ACCESS_DENIED')
    if t.get('status') not in ('DISPUTED','CANCEL_REQUESTED','SELLER_COMPLETED','ESCROWED','ACCEPTED'):raise HTTPException(409,'EVIDENCE_NOT_ALLOWED')
    if not (p.content and p.content.strip()) and not p.file_url:raise HTTPException(400,'EVIDENCE_REQUIRED')
    return q('trade_dispute_evidence').insert({'trade_id':tid,'user_id':user,'evidence_type':p.evidence_type.upper(),'content':p.content.strip() if p.content else None,'file_url':p.file_url}).execute().data[0]

@app.post('/v1/admin/market/withdrawals/{wid}/resolve-v2')
def resolve_withdrawal_v2(wid:int,p:WithdrawalResolveV2|str,user=Depends(auth)):
    require_admin(user);r=one('withdrawal_requests',id=wid)
    if not r or r.get('status')!='PENDING':raise HTTPException(409,'INVALID_WITHDRAWAL')
    if isinstance(p,str):action=p.upper();reference=None;note=None
    else:action=p.action.upper();reference=p.admin_reference;note=p.admin_note
    if action=='PAID':q('withdrawal_requests').update({'status':'PAID','admin_reference':reference,'admin_note':note,'processed_at':now_iso(),'updated_at':now_iso()}).eq('id',wid).execute()
    elif action=='REJECT':db.sb.rpc('hub24_reject_withdrawal',{'p_request_id':wid}).execute();q('withdrawal_requests').update({'admin_note':note,'processed_at':now_iso(),'updated_at':now_iso()}).eq('id',wid).execute()
    else:raise HTTPException(400,'INVALID_ACTION')
    audit(user,'WITHDRAWAL_'+action,'withdrawal',wid,{'reference':reference,'note':note});return {'ok':True,'status':'PAID' if action=='PAID' else 'REJECTED'}

@app.get('/v1/admin/referral-settings')
def get_referral_settings(user=Depends(auth)):
    require_admin(user);s=one('market_settings',id=1) or {};return {'referral_qualification_charge':int(s.get('referral_qualification_charge') or 10000),'referral_monthly_reward_cap':int(s.get('referral_monthly_reward_cap') or 100000)}
@app.put('/v1/admin/referral-settings')
def put_referral_settings(p:ReferralSettings,user=Depends(auth)):
    require_admin(user)
    if p.referral_qualification_charge<0 or p.referral_monthly_reward_cap<0:raise HTTPException(400,'INVALID_REFERRAL_SETTINGS')
    q('market_settings').update({'referral_qualification_charge':p.referral_qualification_charge,'referral_monthly_reward_cap':p.referral_monthly_reward_cap,'updated_at':now_iso()}).eq('id',1).execute();audit(user,'REFERRAL_SETTINGS_CHANGE','market_settings',1,p.model_dump());return {'ok':True,**p.model_dump()}
@app.get('/v1/admin/logs')
def admin_logs(user=Depends(auth)):
    require_admin(user);return {'items':q('admin_logs').select('*').order('created_at',desc=True).limit(500).execute().data or []}
@app.get('/v1/admin/market/trades/{tid}/evidence')
def admin_trade_evidence(tid:int,user=Depends(auth)):
    require_admin(user);return {'items':q('trade_dispute_evidence').select('*').eq('trade_id',tid).order('created_at').execute().data or []}
