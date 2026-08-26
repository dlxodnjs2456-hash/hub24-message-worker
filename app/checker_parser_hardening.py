from . import checker as base


def _parse_export_hardened(raw:bytes):
    rows=base._rows_from_export(raw)
    if not rows:
        return {}
    aliases_phone={'phone','phone_number','mobile','number','tel','telephone','手机号','手机','电话号码','电话','전화번호'}
    aliases_uid={'telegram_id','telegram_user_id','uid','user_id','tg_id','id','텔레그램 uid','텔레그램_uid'}
    aliases_user={'telegram_username','username','tg_username','user_name','텔레그램 id','텔레그램_id'}
    aliases_active={
        'telegram_active','last_seen','active_at','last_online','online_time','active',
        'active_days','activity_days','last_seen_days','last_active','last_activity','online',
        'days','day','접속일자','텔레그램 접속일자','활동일수','접속일수','최근접속','최근 접속'
    }
    first=[str(x or '').strip().lower() for x in rows[0]]
    has_header=any(x in aliases_phone|aliases_uid|aliases_user|aliases_active for x in first)
    start=1 if has_header else 0

    def exact_idx(aliases,default=None):
        if has_header:
            for i,h in enumerate(first):
                if h in aliases:
                    return i
        return default

    def fuzzy_active_idx():
        if not has_header:
            return 3
        needles=('active','activity','last','online','day','date','time','접속','활동')
        for i,h in enumerate(first):
            if any(n in h for n in needles):
                return i
        return None

    pi=exact_idx(aliases_phone,0)
    ui=exact_idx(aliases_uid,1 if not has_header else None)
    ni=exact_idx(aliases_user,2 if not has_header else None)
    ai=exact_idx(aliases_active,None)
    if ai is None:
        ai=fuzzy_active_idx()

    out={}
    for row in rows[start:]:
        if pi is None or pi>=len(row):
            continue
        n=base._normalize(str(row[pi] or ''))
        if not n:
            continue
        active=None
        if ai is not None and ai<len(row) and row[ai] not in (None,''):
            active=str(row[ai]).strip()
        out[n]={
            'status':'REGISTERED',
            'telegram_id':row[ui] if ui is not None and ui<len(row) else None,
            'telegram_username':row[ni] if ni is not None and ni<len(row) else None,
            'telegram_active':active,
        }
    return out


# Apply to the provider engine and restart recovery without changing Telegram send logic.
base._parse_export=_parse_export_hardened
