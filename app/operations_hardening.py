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


def _restricted_retry_failure(target):
    text=(' '.join([str(target.get('error_code') or ''),str(target.get('error_detail') or ''),str(target.get('stage') or '')])).upper()
    markers=('FROZEN','FLOOD_WAIT','RATE_LIMIT','TOO MANY REQUESTS','METHOD THAT IS NOT AVAILABLE FOR FROZEN ACCOUNTS','TELEGRAM_RATE_LIMIT')
    return any(x in text for x in markers)

@app.post('/v1/jobs/{jid}/reuse-cleanup')
def job_reuse_cleanup(jid:int,user=Depends(auth)):
    job=db.one('jobs',user,eq={'id':jid})
    if not job: raise HTTPException(404,'JOB_NOT_FOUND')
    if str(job.get('status') or '').upper()=='RUNNING':
        raise HTTPException(409,'진행 중인 JOB은 정리할 수 없습니다. 먼저 일시정지 또는 중지하세요.')

    targets=db.rows('job_targets',user,eq={'job_id':jid},order='created_at')
    sent=[x for x in targets if str(x.get('state') or '').upper()=='SENT']
    failed=[x for x in targets if str(x.get('state') or '').upper()=='FAILED']
    restricted=[x for x in failed if _restricted_retry_failure(x)]
    retry=[x for x in failed if not _restricted_retry_failure(x)]

    ready=db.rows('telegram_accounts',user,eq={'status':'READY'},order='created_at')
    ready_ids=[int(x['id']) for x in ready]
    if retry and not ready_ids:
        raise HTTPException(409,'FAILED 재배치에 사용할 READY Telegram 계정이 없습니다.')

    for t in sent:
        db.delete('job_targets',eq={'id':t['id'],'user_id':user})

    for i,t in enumerate(retry):
        old=int(t.get('assigned_account_id') or 0)
        candidates=[x for x in ready_ids if x!=old] or ready_ids
        aid=candidates[i%len(candidates)]
        db.update('job_targets',{
            'assigned_account_id':aid,
            'state':'WAITING',
            'stage':'FAILED 재배치 / 발송 대기',
            'error_code':None,
            'error_detail':None,
            'message_id':None,
            'updated_at':now_iso(),
        },eq={'id':t['id'],'user_id':user})

    for t in restricted:
        db.update('job_targets',{
            'stage':'Telegram 제한 실패 / 운영자 확인 필요',
            'updated_at':now_iso(),
        },eq={'id':t['id'],'user_id':user})

    remaining=db.rows('job_targets',user,eq={'job_id':jid},order=None)
    waiting=sum(1 for x in remaining if str(x.get('state') or '').upper() in ('WAITING','PROCESSING'))
    failed_count=sum(1 for x in remaining if str(x.get('state') or '').upper()=='FAILED')
    next_status='WAITING' if waiting>0 else ('PAUSED' if failed_count>0 else 'COMPLETED')
    db.update('jobs',{
        'status':next_status,
        'total_count':len(remaining),
        'sent_count':0,
        'failed_count':failed_count,
        'pending_count':waiting,
        'stop_reason':'RESTRICTED_FAILURES_REQUIRE_OPERATOR' if restricted else None,
        'updated_at':now_iso(),
    },eq={'id':jid,'user_id':user})
    db.event(user,jid,'INFO','JOB',f'재사용 정리 완료 / SENT {len(sent)}건 제외 / FAILED 재배치 {len(retry)}건 / 제한 실패 보류 {len(restricted)}건')
    return {'ok':True,'sent_removed':len(sent),'failed_requeued':len(retry),'restricted_kept':len(restricted),'remaining':len(remaining),'status':next_status}


@app.post('/v1/jobs/{jid}/release-restriction')
def release_job_restriction(jid:int,user=Depends(auth)):
    job=db.one('jobs',user,eq={'id':jid})
    if not job:
        raise HTTPException(404,'JOB_NOT_FOUND')
    if str(job.get('status') or '').upper()=='RUNNING':
        raise HTTPException(409,'진행 중인 JOB은 제한 상태를 해제할 수 없습니다. 먼저 일시정지 상태를 확인하세요.')

    targets=db.rows('job_targets',user,eq={'job_id':jid},order='created_at')
    restricted=[]
    for t in targets:
        state=str(t.get('state') or '').upper()
        if state in ('WAITING','FAILED','CHECK_REQUIRED') and _restricted_retry_failure(t):
            restricted.append(t)

    stop_reason=str(job.get('stop_reason') or '').upper()
    job_marked=('RATE_LIMIT' in stop_reason or 'FROZEN' in stop_reason or 'RESTRICTED' in stop_reason)
    if not restricted and not job_marked:
        raise HTTPException(409,'이 JOB에서 해제할 Telegram 제한 상태가 확인되지 않습니다.')

    for t in restricted:
        db.update('job_targets',{
            'state':'WAITING',
            'stage':'운영자 확인 완료 / 수동 재개 대기',
            'error_code':None,
            'error_detail':None,
            'updated_at':now_iso(),
        },eq={'id':t['id'],'user_id':user})

    remaining=db.rows('job_targets',user,eq={'job_id':jid},order=None)
    waiting=sum(1 for x in remaining if str(x.get('state') or '').upper() in ('WAITING','PROCESSING'))
    failed_count=sum(1 for x in remaining if str(x.get('state') or '').upper()=='FAILED')
    db.update('jobs',{
        'status':'PAUSED',
        'stop_reason':'OPERATOR_RELEASED_PENDING_MANUAL_RESUME',
        'failed_count':failed_count,
        'pending_count':waiting,
        'updated_at':now_iso(),
    },eq={'id':jid,'user_id':user})
    db.event(user,jid,'INFO','RATE_LIMIT',f'운영자 수동 확인 완료 / 제한 상태 {len(restricted)}건 해제 / 자동 재개 안 함')
    return {
        'ok':True,
        'released_count':len(restricted),
        'status':'PAUSED',
        'resume_required':True,
        'message':'내부 제한 상태만 해제했습니다. Telegram 제한 우회가 아니며, 상태 확인 후 시작/재개를 직접 눌러야 합니다.',
    }
