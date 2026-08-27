import asyncio

from fastapi import Depends, HTTPException

from .main import app, auth, now_iso
from . import db
from . import checker as base


def q(t):
    return db.sb.table(t)


@app.post('/v1/checker/jobs/{jid}/cancel')
async def checker_cancel(jid: int, user=Depends(auth)):
    row = q('npay_checker_jobs').select('*').eq('id', jid).eq('user_id', user).maybe_single().execute().data
    if not row:
        raise HTTPException(404, 'CHECKER_JOB_NOT_FOUND')

    status = str(row.get('status') or '').upper()
    if status in ('COMPLETED', 'CANCELLED'):
        raise HTTPException(409, 'CHECKER_JOB_NOT_CANCELLABLE')

    task = base._checker_tasks.get(str(jid))
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    q('npay_checker_jobs').update({
        'status': 'CANCELLED',
        'provider_status': 'CANCELLED_BY_USER',
        'error_message': None,
        'completed_at': now_iso(),
        'updated_at': now_iso(),
    }).eq('id', jid).eq('user_id', user).execute()

    return {'ok': True, 'job_id': jid, 'status': 'CANCELLED'}
