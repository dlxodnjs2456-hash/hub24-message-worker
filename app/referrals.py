from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth
from . import db

def q(table): return db.sb.table(table)
def user_name(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid);u=r.user if r else None
        if not u:return '회원'
        meta=getattr(u,'user_metadata',None) or {};nick=meta.get('nickname') or meta.get('name')
        if nick:return str(nick)[:30]
        email=getattr(u,'email',None) or ''
        if '@' in email:
            local,domain=email.split('@',1)
            local_mask=(local[:2]+'***') if len(local)>2 else ((local[:1]+'*') if local else '***')
            return local_mask+'@'+domain
        return '회원'
    except Exception:return '회원'
def current_month_start_utc():
    tz=ZoneInfo('Asia/Seoul');now=datetime.now(tz);start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0);return start.astimezone(timezone.utc).isoformat()
def settings():
    rows=q('market_settings').select('referral_monthly_reward_cap,referral_qualification_charge,referral_reward_rate').eq('id',1).limit(1).execute().data or []
    return rows[0] if rows else {'referral_monthly_reward_cap':100000,'referral_qualification_charge':10000,'referral_reward_rate':2.0}

class ReferralCodeCreate(BaseModel): code:str

@app.get('/v1/referrals/me')
def referral_me(user=Depends(auth)):
    code_rows=q('npay_referral_codes').select('*').eq('user_id',user).limit(1).execute().data or [];code=code_rows[0]['code'] if code_rows else None
    refs=q('npay_referrals').select('referred_user_id,created_at,qualified_at').eq('referrer_user_id',user).order('created_at',desc=True).limit(5000).execute().data or []
    month_start=current_month_start_utc();month_refs=[x for x in refs if x.get('qualified_at') and str(x.get('qualified_at'))>=month_start];valid_refs=[x for x in refs if x.get('qualified_at')]
    rewards=q('npay_referral_rewards').select('reward_amount,source_type,created_at').eq('referrer_user_id',user).eq('source_type','CHARGE').order('created_at',desc=True).limit(5000).execute().data or []
    total_reward=sum(int(x.get('reward_amount') or 0) for x in rewards);month_reward=sum(int(x.get('reward_amount') or 0) for x in rewards if str(x.get('created_at') or '')>=month_start);s=settings()
    return {'code':code,'total_referrals':len(refs),'valid_referrals':len(valid_refs),'monthly_referrals':len(month_refs),'total_reward':total_reward,'monthly_reward':month_reward,'reward_rate_percent':float(s.get('referral_reward_rate') or 2.0),'reward_basis':'APPROVED_CHARGE_ONLY','qualification_charge':int(s.get('referral_qualification_charge') or 10000),'monthly_reward_cap_per_referred':int(s.get('referral_monthly_reward_cap') or 100000),'code_rule':'4~10자 영문/숫자'}

@app.get('/v1/referrals/members')
def referral_members(user=Depends(auth)):
    refs=q('npay_referrals').select('referred_user_id,created_at,qualified_at').eq('referrer_user_id',user).order('created_at',desc=True).limit(1000).execute().data or []
    month_start=current_month_start_utc(); items=[]
    for r in refs:
        uid=str(r.get('referred_user_id') or '')
        if not uid: continue
        charges=q('point_charge_requests').select('id,amount,status,created_at').eq('user_id',uid).eq('status','APPROVED').order('created_at').limit(500).execute().data or []
        first=charges[0] if charges else None
        total_deposit=sum(int(x.get('amount') or 0) for x in charges)
        rewards=q('npay_referral_rewards').select('reward_amount,created_at').eq('referrer_user_id',user).eq('referred_user_id',uid).eq('source_type','CHARGE').limit(1000).execute().data or []
        total_reward=sum(int(x.get('reward_amount') or 0) for x in rewards)
        monthly_reward=sum(int(x.get('reward_amount') or 0) for x in rewards if str(x.get('created_at') or '')>=month_start)
        items.append({'user_id':uid,'name':user_name(uid),'joined_at':r.get('created_at'),'qualified':bool(r.get('qualified_at')),'qualified_at':r.get('qualified_at'),'first_approved_deposit':int(first.get('amount') or 0) if first else 0,'first_approved_deposit_at':first.get('created_at') if first else None,'total_approved_deposit':total_deposit,'monthly_reward':monthly_reward,'total_reward':total_reward})
    return {'items':items,'count':len(items)}

@app.post('/v1/referrals/code')
def issue_referral_code(p:ReferralCodeCreate,user=Depends(auth)):
    old=q('npay_referral_codes').select('code').eq('user_id',user).limit(1).execute().data or []
    if old:return {'code':old[0]['code'],'already_exists':True}
    code=re.sub(r'\s+','',str(p.code or '')).upper()
    if not re.fullmatch(r'[A-Z0-9]{4,10}',code):raise HTTPException(400,'REFERRAL_CODE_4_TO_10_ALNUM')
    used=q('npay_referral_codes').select('user_id').eq('code',code).limit(1).execute().data or []
    if used:raise HTTPException(409,'REFERRAL_CODE_ALREADY_USED')
    try:
        q('npay_referral_codes').insert({'user_id':user,'code':code}).execute();return {'code':code,'already_exists':False}
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():raise HTTPException(409,'REFERRAL_CODE_ALREADY_USED')
        raise HTTPException(400,str(e)[:300])

@app.get('/v1/referrals/leaderboard')
def referral_leaderboard(user=Depends(auth)):
    month_start=current_month_start_utc();rows=q('npay_referrals').select('referrer_user_id,qualified_at').gte('qualified_at',month_start).limit(10000).execute().data or [];counts={}
    for r in rows:
        uid=str(r.get('referrer_user_id'));counts[uid]=counts.get(uid,0)+1
    reward_rows=q('npay_referral_rewards').select('referrer_user_id,reward_amount,created_at').eq('source_type','CHARGE').gte('created_at',month_start).limit(10000).execute().data or [];rewards={}
    for r in reward_rows:
        uid=str(r.get('referrer_user_id'));rewards[uid]=rewards.get(uid,0)+int(r.get('reward_amount') or 0)
    ranked=sorted(counts.items(),key=lambda x:(-x[1],-rewards.get(x[0],0),x[0]))[:50];items=[]
    for idx,(uid,count) in enumerate(ranked,1):items.append({'rank':idx,'user_id':uid,'name':user_name(uid),'referral_count':count,'reward_amount':rewards.get(uid,0),'is_me':uid==str(user)})
    return {'month':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m'),'items':items,'king':items[0] if items else None,'qualification':'첫 승인 입금 기준을 충족한 추천회원만 집계'}
