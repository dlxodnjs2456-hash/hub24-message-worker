from fastapi import Depends, HTTPException
from pydantic import BaseModel

from . import db
from .security import enc
from .telegram import client_from_account
from .main import app, auth, event, now_iso, recalc_job, _tasks


class ContactDeleteRequest(BaseModel):
    ids: list[str | int]


class ProxyUpdateRequest(BaseModel):
    proxy_url: str | None = None


def _ensure_batch_editable(user: str, bid: str):
    batch = db.one('contact_batches', user, eq={'id': bid})
    if not batch:
        raise HTTPException(404, 'batch not found')
    used = db.rows('jobs', user, eq={'batch_id': bid}, order=None, limit=1)
    if used:
        raise HTTPException(409, '이미 작업에 사용된 DB는 연락처를 삭제할 수 없습니다.')
    return batch


def _sync_batch_count(user: str, bid: str):
    rows = db.rows('contacts', user, eq={'batch_id': bid}, order=None)
    db.update('contact_batches', {'total_count': len(rows)}, eq={'id': bid, 'user_id': user})
    return len(rows)


@app.get('/v1/batches/{bid}/contacts')
def batch_contacts(bid: str, limit: int = 5000, user=Depends(auth)):
    batch = db.one('contact_batches', user, eq={'id': bid})
    if not batch:
        raise HTTPException(404, 'batch not found')
    items = db.rows('contacts', user, eq={'batch_id': bid}, order='created_at', limit=min(limit, 5000))
    for item in items:
        p = item.get('phone') or ''
        item['phone_display'] = f'{p[:3]}-{p[3:7]}-{p[7:]}' if len(p) == 11 else p
    return {'batch': batch, 'items': items}


@app.post('/v1/batches/{bid}/contacts/delete')
def delete_selected_contacts(bid: str, p: ContactDeleteRequest, user=Depends(auth)):
    _ensure_batch_editable(user, bid)
    ids = {str(x) for x in p.ids if str(x)}
    deleted = 0
    for cid in ids:
        row = db.one('contacts', user, eq={'id': cid, 'batch_id': bid})
        if not row:
            continue
        db.delete('contacts', eq={'id': cid, 'batch_id': bid, 'user_id': user})
        deleted += 1
    remaining = _sync_batch_count(user, bid)
    return {'ok': True, 'deleted': deleted, 'remaining': remaining}


@app.delete('/v1/batches/{bid}/contacts')
def delete_all_contacts(bid: str, user=Depends(auth)):
    _ensure_batch_editable(user, bid)
    before = len(db.rows('contacts', user, eq={'batch_id': bid}, order=None))
    db.delete('contacts', eq={'batch_id': bid, 'user_id': user})
    _sync_batch_count(user, bid)
    return {'ok': True, 'deleted': before, 'remaining': 0}


@app.delete('/v1/batches/{bid}')
def delete_batch(bid: str, user=Depends(auth)):
    _ensure_batch_editable(user, bid)
    db.delete('contacts', eq={'batch_id': bid, 'user_id': user})
    db.delete('contact_batches', eq={'id': bid, 'user_id': user})
    return {'ok': True}


@app.post('/v1/jobs/{jid}/reset-processing')
def reset_processing(jid: str, user=Depends(auth)):
    job = db.one('jobs', user, eq={'id': jid})
    if not job:
        raise HTTPException(404, 'job not found')
    if jid in _tasks and not _tasks[jid].done():
        raise HTTPException(409, '실행 중인 작업은 먼저 일시정지 또는 중지하세요.')
    rows = db.rows('job_targets', user, eq={'job_id': jid, 'state': 'PROCESSING'}, order=None)
    for row in rows:
        db.update('job_targets', {
            'state': 'WAITING',
            'stage': '대기',
            'error_code': None,
            'error_detail': None,
            'updated_at': now_iso(),
        }, eq={'id': row['id'], 'user_id': user})
    recalc_job(user, jid)
    db.update('jobs', {'status': 'WAITING', 'stop_reason': None, 'updated_at': now_iso()}, eq={'id': jid, 'user_id': user})
    event(user, jid, 'INFO', 'JOB', f'진행중 초기화 / {len(rows)}건 WAITING 복구')
    return {'ok': True, 'reset_count': len(rows), 'status': 'WAITING'}


@app.post('/v1/jobs/{jid}/reassign')
def reassign_waiting(jid: str, user=Depends(auth)):
    job = db.one('jobs', user, eq={'id': jid})
    if not job:
        raise HTTPException(404, 'job not found')
    if jid in _tasks and not _tasks[jid].done():
        raise HTTPException(409, '실행 중인 작업은 먼저 일시정지 또는 중지하세요.')
    accounts = db.rows('telegram_accounts', user, eq={'status': 'READY'}, order='created_at')
    if not accounts:
        raise HTTPException(400, 'READY SESSION account required')
    waiting = db.rows('job_targets', user, eq={'job_id': jid, 'state': 'WAITING'}, order='created_at')
    for i, row in enumerate(waiting):
        aid = accounts[i % len(accounts)]['id']
        db.update('job_targets', {
            'assigned_account_id': aid,
            'stage': '대기 · 재배정',
            'updated_at': now_iso(),
        }, eq={'id': row['id'], 'user_id': user})
    event(user, jid, 'INFO', 'JOB', f'대기 대상 재배정 / {len(waiting)}건 / READY 계정 {len(accounts)}개')
    return {'ok': True, 'reassigned': len(waiting), 'accounts': len(accounts)}


@app.put('/v1/accounts/{aid}/proxy')
def update_proxy(aid: str, p: ProxyUpdateRequest, user=Depends(auth)):
    account = db.one('telegram_accounts', user, eq={'id': aid})
    if not account:
        raise HTTPException(404, 'account not found')
    value = (p.proxy_url or '').strip()
    db.update('telegram_accounts', {
        'proxy_url_enc': enc(value) if value else None,
        'last_check_at': None,
        'updated_at': now_iso(),
    }, eq={'id': aid, 'user_id': user})
    return {'ok': True, 'proxy_enabled': bool(value)}


@app.get('/v1/accounts/{aid}/dialogs')
async def account_dialogs(aid: str, limit: int = 50, user=Depends(auth)):
    account = db.one('telegram_accounts', user, eq={'id': aid})
    if not account:
        raise HTTPException(404, 'account not found')
    c = await client_from_account(account)
    try:
        await c.connect()
        if not await c.is_user_authorized():
            raise HTTPException(409, 'SESSION_NOT_AUTHORIZED')
        dialogs = await c.get_dialogs(limit=min(limit, 100))
        items = []
        for d in dialogs:
            entity = d.entity
            items.append({
                'id': str(getattr(entity, 'id', '')),
                'name': d.name or '',
                'is_user': bool(getattr(d, 'is_user', False)),
                'is_group': bool(getattr(d, 'is_group', False)),
                'is_channel': bool(getattr(d, 'is_channel', False)),
                'unread_count': int(getattr(d, 'unread_count', 0) or 0),
            })
        return {'items': items}
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass


@app.get('/v1/accounts/{aid}/dialogs/{peer_id}/messages')
async def dialog_messages(aid: str, peer_id: int, limit: int = 50, user=Depends(auth)):
    account = db.one('telegram_accounts', user, eq={'id': aid})
    if not account:
        raise HTTPException(404, 'account not found')
    c = await client_from_account(account)
    try:
        await c.connect()
        if not await c.is_user_authorized():
            raise HTTPException(409, 'SESSION_NOT_AUTHORIZED')
        entity = await c.get_entity(peer_id)
        messages = await c.get_messages(entity, limit=min(limit, 100))
        return {'items': [{
            'id': int(m.id),
            'date': m.date.isoformat() if m.date else None,
            'out': bool(m.out),
            'text': m.message or '',
        } for m in messages]}
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass
