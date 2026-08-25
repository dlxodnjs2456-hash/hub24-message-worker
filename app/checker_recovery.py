import asyncio
import httpx

from .main import app
from . import db
from .settings import settings
from . import checker as base


def q(t):
    return db.sb.table(t)


async def _finish_existing_job(job):
    jid=job['id']; user=job['user_id']; task_ids=job.get('api_task_ids') or []
    if not task_ids:
        return
    timeout=max(5,int(settings.check_api_timeout_seconds or 30))
    found={}
    try:
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=True) as client:
            for tid in task_ids:
                st=await base._wait_provider_task(client,str(tid))
                q('npay_checker_jobs').update({'provider_status':st,'provider_last_check_at':base.now_iso(),'updated_at':base.now_iso()}).eq('id',jid).eq('user_id',user).execute()
                raw=await base._export_provider_task(client,str(tid))
                found.update(base._parse_export(raw))
        items=q('npay_checker_items').select('id,normalized_phone').eq('job_id',jid).eq('user_id',user).order('id').execute().data or []
        checked=base.now_iso()
        for x in items:
            v=found.get(x['normalized_phone'])
            upd={'checked_at':checked,'api_status':'200'}
            if v:
                upd.update(v)
            else:
                upd.update({'status':'NOT_REGISTERED','telegram_id':None,'telegram_username':None,'telegram_active':None})
            q('npay_checker_items').update(upd).eq('id',x['id']).eq('user_id',user).execute()
        rows=q('npay_checker_items').select('status').eq('job_id',jid).eq('user_id',user).execute().data or []
        done=len(rows)
        reg=sum(1 for x in rows if x['status']=='REGISTERED')
        unk=sum(1 for x in rows if x['status'] in ('UNKNOWN','NOT_REGISTERED'))
        err=sum(1 for x in rows if x['status'] in ('API_ERROR','RATE_LIMITED','TIMEOUT'))
        q('npay_checker_jobs').update({'status':'COMPLETED','provider_status':'COMPLETED','completed_count':done,'registered_count':reg,'unknown_count':unk,'error_count':err,'completed_at':base.now_iso(),'updated_at':base.now_iso()}).eq('id',jid).eq('user_id',user).execute()
    except Exception as e:
        q('npay_checker_jobs').update({'provider_status':'RECOVERY_WAIT','error_message':f'RECOVERY:{str(e)[:900]}','updated_at':base.now_iso()}).eq('id',jid).eq('user_id',user).execute()


async def _recover_all():
    await asyncio.sleep(2)
    rows=q('npay_checker_jobs').select('id,user_id,status,api_task_ids,provider_status').eq('status','RUNNING').execute().data or []
    for job in rows:
        if job.get('api_task_ids'):
            asyncio.create_task(_finish_existing_job(job))


@app.on_event('startup')
async def recover_checker_jobs():
    asyncio.create_task(_recover_all())
