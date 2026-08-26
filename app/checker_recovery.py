import asyncio
import httpx

from .main import app
from . import db
from .settings import settings
from . import checker as base


def q(t):
    return db.sb.table(t)


_recovering=set()


def _result_rows(found):
    out=[]
    for phone,v in (found or {}).items():
        out.append({
            'normalized_phone':phone,
            'telegram_id':None if v.get('telegram_id') in (None,'') else str(v.get('telegram_id')),
            'telegram_username':None if v.get('telegram_username') in (None,'') else str(v.get('telegram_username')),
            'telegram_active':None if v.get('telegram_active') in (None,'') else str(v.get('telegram_active')),
        })
    return out


async def _finish_existing_job(job):
    jid=job['id']; user=job['user_id']; task_ids=job.get('api_task_ids') or []
    key=str(jid)
    if not task_ids or key in _recovering:
        return
    _recovering.add(key)
    timeout=max(5,int(settings.check_api_timeout_seconds or 30))
    found={}
    try:
        q('npay_checker_jobs').update({'provider_status':'RECOVERING','provider_last_check_at':base.now_iso(),'updated_at':base.now_iso()}).eq('id',jid).eq('user_id',user).execute()
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True) as client:
            for tid in task_ids:
                st=await base._wait_provider_task(client,str(tid))
                q('npay_checker_jobs').update({'provider_status':st,'provider_last_check_at':base.now_iso(),'updated_at':base.now_iso()}).eq('id',jid).eq('user_id',user).execute()
                raw=await base._export_provider_task(client,str(tid))
                found.update(base._parse_export(raw))
        # Apply every result and finish the job in one DB transaction. This prevents
        # a Worker restart from leaving only part of the file written.
        db.sb.rpc('npay_finalize_checker_job',{
            'p_user':user,
            'p_job_id':jid,
            'p_results':_result_rows(found),
        }).execute()
    except Exception as e:
        q('npay_checker_jobs').update({'provider_status':'RECOVERY_WAIT','error_message':f'RECOVERY:{str(e)[:900]}','updated_at':base.now_iso()}).eq('id',jid).eq('user_id',user).execute()
    finally:
        _recovering.discard(key)


async def _recover_once():
    rows=q('npay_checker_jobs').select('id,user_id,status,api_task_ids,provider_status').eq('status','RUNNING').execute().data or []
    for job in rows:
        if job.get('api_task_ids') and str(job['id']) not in _recovering:
            asyncio.create_task(_finish_existing_job(job))


async def _recovery_loop():
    # Do one quick pass after startup, then keep watching. A transient provider
    # error or another Worker restart no longer strands a paid checker job.
    await asyncio.sleep(2)
    while True:
        try:
            await _recover_once()
        except Exception:
            pass
        await asyncio.sleep(30)


@app.on_event('startup')
async def recover_checker_jobs():
    asyncio.create_task(_recovery_loop())
