from datetime import datetime, timezone
from supabase import create_client
from .settings import settings

def _now():
    return datetime.now(timezone.utc).isoformat()

if not settings.supabase_url or not settings.supabase_service_role_key:
    raise RuntimeError('HUB24_SUPABASE_URL and HUB24_SUPABASE_SERVICE_ROLE_KEY are required')

sb=create_client(settings.supabase_url,settings.supabase_service_role_key)

def rows(table, user_id, *, eq=None, order='id', desc=False, limit=None, select='*'):
    q=sb.table(table).select(select).eq('user_id',user_id)
    for k,v in (eq or {}).items():
        q=q.eq(k,v)
    if order:
        q=q.order(order,desc=desc)
    if limit:
        q=q.limit(limit)
    return q.execute().data or []

def one(table,user_id,*,eq=None,select='*'):
    data=rows(table,user_id,eq=eq,limit=1,select=select)
    return data[0] if data else None

def insert(table,payload):
    data=sb.table(table).insert(payload).execute().data or []
    return data[0] if data else None

def insert_many(table,payloads):
    if not payloads:return []
    return sb.table(table).insert(payloads).execute().data or []

def update(table,payload,*,eq):
    q=sb.table(table).update(payload)
    for k,v in eq.items():q=q.eq(k,v)
    return q.execute().data or []

def delete(table,*,eq):
    q=sb.table(table).delete()
    for k,v in eq.items():q=q.eq(k,v)
    return q.execute().data or []

def event(user_id,job_id,level,scope,message,account_id=None):
    return insert('job_logs',{
        'user_id':user_id,'job_id':job_id,'account_id':account_id,
        'level':level,'scope':scope,'message':message,'created_at':_now()
    })
