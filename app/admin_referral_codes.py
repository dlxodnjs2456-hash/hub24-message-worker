from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .main import app, auth
from . import db
from .marketplace import require_admin


def q(table):
    return db.sb.table(table)


def member_label(uid):
    try:
        r = db.sb.auth.admin.get_user_by_id(str(uid))
        u = r.user if r else None
        if not u:
            return {'email': '-', 'name': '회원'}
        meta = getattr(u, 'user_metadata', None) or {}
        return {
            'email': getattr(u, 'email', None) or '-',
            'name': str(meta.get('nickname') or meta.get('name') or '회원')[:60],
        }
    except Exception:
        return {'email': '-', 'name': '회원'}


class ReferralCodeStatus(BaseModel):
    is_active: bool


@app.get('/v1/admin/referral-codes')
def admin_referral_codes(user=Depends(auth)):
    require_admin(user)
    codes = q('npay_referral_codes').select('user_id,code,is_active,created_at').order('created_at', desc=True).limit(2000).execute().data or []
    refs = q('npay_referrals').select('referrer_user_id,referred_user_id,qualified_at').limit(10000).execute().data or []
    rewards = q('npay_referral_rewards').select('referrer_user_id,reward_amount,source_type').eq('source_type', 'CHARGE').limit(10000).execute().data or []

    stats = {}
    for r in refs:
        uid = str(r.get('referrer_user_id') or '')
        if not uid:
            continue
        s = stats.setdefault(uid, {'referrals': 0, 'qualified': 0, 'reward': 0})
        s['referrals'] += 1
        if r.get('qualified_at'):
            s['qualified'] += 1
    for r in rewards:
        uid = str(r.get('referrer_user_id') or '')
        if not uid:
            continue
        s = stats.setdefault(uid, {'referrals': 0, 'qualified': 0, 'reward': 0})
        s['reward'] += int(r.get('reward_amount') or 0)

    items = []
    for c in codes:
        uid = str(c.get('user_id') or '')
        profile = member_label(uid)
        s = stats.get(uid, {'referrals': 0, 'qualified': 0, 'reward': 0})
        items.append({
            'user_id': uid,
            'code': c.get('code'),
            'is_active': bool(c.get('is_active')),
            'created_at': c.get('created_at'),
            'member_name': profile['name'],
            'member_email': profile['email'],
            'referral_count': int(s['referrals']),
            'qualified_count': int(s['qualified']),
            'total_reward': int(s['reward']),
        })
    return {'items': items, 'count': len(items)}


@app.put('/v1/admin/referral-codes/{code}')
def admin_referral_code_status(code: str, p: ReferralCodeStatus, user=Depends(auth)):
    require_admin(user)
    value = str(code or '').strip().upper()
    rows = q('npay_referral_codes').select('code').eq('code', value).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, 'REFERRAL_CODE_NOT_FOUND')
    q('npay_referral_codes').update({'is_active': bool(p.is_active)}).eq('code', value).execute()
    return {'ok': True, 'code': value, 'is_active': bool(p.is_active)}
