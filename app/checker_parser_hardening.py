from . import checker as base


def _looks_active(v):
    if v in (None,''):
        return False
    s=str(v).strip()
    try:
        float(s)
        return True
    except Exception:
        pass
    sl=s.lower()
    return any(x in sl for x in ('day','hour','min','online','recent','today','yesterday','일','시간','분'))


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
            return None
        needles=('active','activity','last','online','day','date','time','접속','활동')
        for i,h in enumerate(first):
            if any(n in h for n in needles):
                return i
        return None

    pi=exact_idx(aliases_phone,0)
    ui=exact_idx(aliases_uid,1 if not has_header else None)
    ni=exact_idx(aliases_user,None)
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

        # Provider exports without a header are commonly:
        # phone, telegram_uid, telegram_active
        # or phone, telegram_uid, telegram_username, telegram_active.
        # Do not mistake a numeric activity value such as 0 for username.
        row_ni=ni
        row_ai=ai
        if not has_header:
            if len(row)==3:
                row_ni=None
                row_ai=2
            elif len(row)>=4:
                if _looks_active(row[2]) and not _looks_active(row[3]):
                    row_ai=2
                    row_ni=3
                else:
                    row_ni=2
                    row_ai=3

        active=None
        if row_ai is not None and row_ai<len(row) and row[row_ai] not in (None,''):
            active=str(row[row_ai]).strip()
        username=None
        if row_ni is not None and row_ni<len(row) and row[row_ni] not in (None,''):
            username=str(row[row_ni]).strip()

        out[n]={
            'status':'REGISTERED',
            'telegram_id':row[ui] if ui is not None and ui<len(row) else None,
            'telegram_username':username,
            'telegram_active':active,
        }
    return out


# Apply only to checker provider parsing/recovery. Telegram send logic is untouched.
base._parse_export=_parse_export_hardened
