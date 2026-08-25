from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import secrets
from fastapi import Depends, HTTPException
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
            local=email.split('@')[0]
            if len(local)<=2:return local[0]+'*' if local else '회원'
            return local[:2]+'***'
        return '회원'
    except Exception:return '회원'

def current_month_start_utc():
    tz=ZoneInfo('Asia/Seoul');now=datetime.now(tz);start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    return start.astimezone(timezone.utc).isoformat()

def settings():
    rows=q('market_settings').select('referral_monthly_reward_cap,referral_qualification_charge').eq('id',1).limit(1).execute().data or []
    return rows[0] if rows else {'referral_monthly_reward_cap':100000,'referral_qualification_charge':10000}

@app.get('/v1/referrals/me')
def referral_me(user=Depends(auth)):
    code_rows=q('referral_codes').select('*').eq('user_id',user).limit(1).execute().data or [];code=code_rows[0]['code'] if code_rows else None
    refs=q('referrals').select('referred_user_id,created_at,qualified_at').eq('referrer_user_id',user).order('created_at',desc=True).limit(5000).execute().data or []
    month_start=current_month_start_utc();month_refs=[x for x in refs if x.get('qualified_at') and str(x.get('qualified_at'))>=month_start]
    valid_refs=[x for x in refs if x.get('qualified_at')]
    rewards=q('referral_rewards').select('reward_amount,source_type,created_at').eq('referrer_user_id',user).order('created_at',desc=True).limit(5000).execute().data or []
    total_reward=sum(int(x.get('reward_amount') or 0) for x in rewards);month_reward=sum(int(x.get('reward_amount') or 0) for x in rewards if str(x.get('created_at') or '')>=month_start);s=settings()
    return {'code':code,'total_referrals':len(refs),'valid_referrals':len(valid_refs),'monthly_referrals':len(month_refs),'total_reward':total_reward,'monthly_reward':month_reward,'reward_rate_percent':1,'qualification_charge':int(s.get('referral_qualification_charge') or 10000),'monthly_reward_cap_per_referred':int(s.get('referral_monthly_reward_cap') or 100000)}

@app.post('/v1/referrals/code')
def issue_referral_code(user=Depends(auth)):
    old=q('referral_codes').select('code').eq('user_id',user).limit(1).execute().data or []
    if old:return {'code':old[0]['code']}
    for _ in range(30):
        code='NP'+secrets.token_hex(4).upper()
        if not (q('referral_codes').select('code').eq('code',code).limit(1).execute().data or []):
            q('referral_codes').insert({'user_id':user,'code':code}).execute();return {'code':code}
    raise HTTPException(500,'REFERRAL_CODE_GENERATION_FAILED')

@app.get('/v1/referrals/leaderboard')
def referral_leaderboard(user=Depends(auth)):
    month_start=current_month_start_utc()
    rows=q('referrals').select('referrer_user_id,qualified_at').not_.is_('qualified_at','null').gte('qualified_at',month_start).limit(10000).execute().data or []
    counts={}
    for r in rows:
        uid=str(r.get('referrer_user_id'));counts[uid]=counts.get(uid,0)+1
    reward_rows=q('referral_rewards').select('referrer_user_id,reward_amount,created_at').gte('created_at',month_start).limit(10000).execute().data or [];rewards={}
    for r in reward_rows:
        uid=str(r.get('referrer_user_id'));rewards[uid]=rewards.get(uid,0)+int(r.get('reward_amount') or 0)
    ranked=sorted(counts.items(),key=lambda x:(-x[1],-rewards.get(x[0],0),x[0]))[:50];items=[]
    for idx,(uid,count) in enumerate(ranked,1):items.append({'rank':idx,'user_id':uid,'name':user_name(uid),'referral_count':count,'reward_amount':rewards.get(uid,0),'is_me':uid==str(user)})
    return {'month':datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m'),'items':items,'king':items[0] if items else None,'qualification':'첫 승인 충전 기준을 충족한 추천회원만 집계'}
