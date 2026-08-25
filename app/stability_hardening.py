from fastapi import Depends, HTTPException

from .main import app, auth, now_iso
from . import db


def q(table):
    return db.sb.table(table)


@app.post('/v1/market/products/{pid}/buy')
def buy_product_atomic(pid: int, payload: dict, user=Depends(auth)):
    try:
        quantity = max(1, int((payload or {}).get('quantity') or 1))
    except Exception:
        raise HTTPException(400, 'INVALID_QUANTITY')
    try:
        trade_id = db.sb.rpc('npay_buy_product_atomic', {
            'p_buyer': user,
            'p_product_id': pid,
            'p_quantity': quantity,
        }).execute().data
        return {'ok': True, 'trade_id': trade_id}
    except Exception as e:
        msg = str(e)
        if 'OUT_OF_STOCK' in msg:
            raise HTTPException(409, 'OUT_OF_STOCK')
        if 'PRODUCT_NOT_FOUND' in msg:
            raise HTTPException(404, 'PRODUCT_NOT_FOUND')
        if 'INSUFFICIENT_POINTS' in msg:
            raise HTTPException(409, 'INSUFFICIENT_POINTS')
        if 'SELF_TRADE_NOT_ALLOWED' in msg:
            raise HTTPException(409, 'SELF_TRADE_NOT_ALLOWED')
        raise HTTPException(400, msg[:300])


@app.on_event('startup')
def recover_orphan_running_jobs():
    """A Render restart cannot safely resume in-memory Telegram tasks.
    Mark only the job container as PAUSED. PROCESSING targets are intentionally
    left untouched so an operator can inspect/reset them without duplicate sends.
    """
    try:
        rows = q('jobs').select('id,user_id,status').eq('status', 'RUNNING').limit(1000).execute().data or []
        for row in rows:
            q('jobs').update({'status': 'PAUSED', 'updated_at': now_iso()}).eq('id', row['id']).eq('status', 'RUNNING').execute()
            try:
                q('job_logs').insert({
                    'user_id': row['user_id'],
                    'job_id': row['id'],
                    'level': 'WARN',
                    'scope': 'RECOVERY',
                    'message': 'Worker 재시작이 감지되어 작업을 자동 재개하지 않고 PAUSED 처리했습니다. 진행중 대상 확인 후 운영자가 재개하세요.',
                    'created_at': now_iso(),
                }).execute()
            except Exception:
                pass
    except Exception:
        pass
