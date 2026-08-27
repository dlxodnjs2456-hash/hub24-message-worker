from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from .main import app, auth, event
from . import db


class ImageJobCreate(BaseModel):
    batch_id: int
    post_code: str
    account_ids: list[int] = Field(default_factory=list)
    contacts_per_account: int = Field(default=50, ge=1, le=1000)
    delay_min: float = 2
    delay_max: float = 5
    global_dedupe: bool = True


def _current_run_contacts(user, batch, checked_ids):
    """Return only contacts resolved by the latest contact-import run.

    Older successful contact imports may still be RESOLVED in the same DB. The
    latest batch progress tells us how many contacts each account resolved in
    the current run, so only the newest N resolved rows for that account are
    eligible for the next image job.
    """
    checked_set = set(checked_ids)
    progress = batch.get('contact_import_account_progress') or {}
    all_rows = db.rows('contacts', user, eq={'batch_id': batch['id'], 'state': 'RESOLVED'}, order='created_at')
    by_account = {}
    for row in all_rows:
        aid = int(row.get('assigned_account_id') or 0)
        if aid in checked_set and row.get('telegram_user_id'):
            by_account.setdefault(aid, []).append(row)

    selected = []
    current_counts = {}
    for aid in checked_ids:
        p = progress.get(str(aid)) or {}
        expected = max(0, int(p.get('resolved') or 0))
        rows = by_account.get(aid, [])
        if expected <= 0 or not rows:
            current_counts[aid] = 0
            continue
        take = rows[-expected:]
        selected.extend(take)
        current_counts[aid] = len(take)
    return selected, current_counts


@app.post('/v1/image-jobs')
def create_image_job(p: ImageJobCreate, user=Depends(auth)):
    stage = 'validate'
    try:
        code = str(p.post_code or '').strip()
        if not code:
            raise HTTPException(400, 'POSTBOT_CODE_REQUIRED')

        stage = 'batch'
        batch = db.one('contact_batches', user, eq={'id': p.batch_id})
        if not batch:
            raise HTTPException(404, 'batch not found')
        if str(batch.get('contact_import_status') or '') != 'COMPLETED':
            raise HTTPException(409, '먼저 연락처 추가 작업을 완료하세요.')

        stage = 'accounts'
        requested = []
        for raw in (p.account_ids or []):
            try:
                aid = int(raw)
            except Exception:
                continue
            if aid not in requested:
                requested.append(aid)
        if not requested:
            raise HTTPException(400, '발송할 READY Telegram 계정을 1개 이상 체크하세요.')

        ready = db.rows('telegram_accounts', user, eq={'status': 'READY'}, order='created_at')
        ready_map = {int(a['id']): a for a in ready}
        checked_ids = [aid for aid in requested if aid in ready_map]
        if not checked_ids:
            raise HTTPException(400, '체크한 계정 중 현재 READY 상태인 Telegram 계정이 없습니다.')
        unavailable = [aid for aid in requested if aid not in ready_map]
        if unavailable:
            raise HTTPException(409, f'체크한 계정 중 READY가 아닌 계정이 있습니다: {unavailable}')

        stage = 'current_run_targets'
        contacts, target_counts = _current_run_contacts(user, batch, checked_ids)
        effective_ids = [aid for aid in checked_ids if int(target_counts.get(aid, 0)) > 0]
        effective_set = set(effective_ids)
        excluded_zero_target_ids = [aid for aid in checked_ids if aid not in effective_set]
        if not contacts or not effective_ids:
            raise HTTPException(400, '체크한 계정 중 이번 연락처 추가에서 확인 성공한 대상이 없습니다.')

        stage = 'reuse_check'
        existing_jobs = db.rows('jobs', user, eq={'batch_id': p.batch_id}, order='created_at', desc=True)
        for old in existing_jobs:
            if str(old.get('operation_mode') or '') != 'IMAGE_POSTBOT':
                continue
            if str(old.get('status') or '').upper() not in ('WAITING', 'RUNNING', 'PAUSED'):
                continue
            if str(old.get('message_text') or '') != code:
                continue
            old_ids = {int(x) for x in (old.get('selected_account_ids') or [])}
            if old_ids != effective_set:
                continue
            targets = db.rows('job_targets', user, eq={'job_id': old['id']}, order=None)
            if not targets:
                continue
            if any(int(t.get('assigned_account_id') or 0) not in effective_set for t in targets):
                continue
            result = dict(old)
            result['assigned_count'] = len(targets)
            result['requested_account_count'] = len(checked_ids)
            result['selected_account_count'] = len(effective_ids)
            result['selected_account_ids'] = effective_ids
            result['excluded_zero_target_account_ids'] = excluded_zero_target_ids
            result['account_target_counts'] = target_counts
            result['reused'] = True
            event(user, old['id'], 'INFO', 'JOB', f'기존 이미지 발송 JOB 재사용 / 체크 {len(checked_ids)}개 / 실제 작업 {len(effective_ids)}개 / 대상 {len(targets)}건')
            return result

        stage = 'wallet'
        required_points = len(contacts) * 15
        # point_wallets has no id column, so do not use db.one(), whose default
        # ordering is by id. Read the row directly by user_id instead.
        wallet_rows = db.sb.table('point_wallets').select('user_id,available_balance').eq('user_id', user).limit(1).execute().data or []
        wallet = wallet_rows[0] if wallet_rows else {}
        available_points = int(wallet.get('available_balance') or 0)
        if available_points < required_points:
            possible = available_points // 15
            raise HTTPException(409, f'발송 포인트가 부족합니다. 필요 {required_points:,}P / 보유 {available_points:,}P / 현재 최대 {possible:,}건 발송 가능')

        stage = 'job_insert'
        # IMAGE_POSTBOT never uses a bot token. Keep a non-empty schema marker
        # instead of invoking encryption for an unused empty value.
        job = db.insert('jobs', {
            'user_id': user,
            'batch_id': p.batch_id,
            'status': 'WAITING',
            'operation_mode': 'IMAGE_POSTBOT',
            'message_text': code,
            'button_text': '',
            'button_url': '',
            'bot_username': 'PostBot',
            'bot_token_enc': 'IMAGE_POSTBOT_UNUSED',
            'delay_min': p.delay_min,
            'delay_max': p.delay_max,
            'global_dedupe': p.global_dedupe,
            'total_count': len(contacts),
            'pending_count': len(contacts),
            'selected_account_ids': effective_ids,
            'contacts_per_account': int(batch.get('contact_import_per_account') or p.contacts_per_account or 50),
            'source_batch_total': int(batch.get('total_count') or len(contacts)),
        })
        if not job or not job.get('id'):
            raise RuntimeError('JOB_INSERT_RETURNED_EMPTY')

        jid = job['id']
        stage = 'target_insert'
        targets = [{
            'user_id': user,
            'job_id': jid,
            'contact_id': c['id'],
            'phone': c['phone'],
            'telegram_user_id': c.get('telegram_user_id'),
            'assigned_account_id': int(c['assigned_account_id']),
            'state': 'WAITING',
            'stage': '이미지 발송 대기',
        } for c in contacts]
        db.insert_many('job_targets', targets)

        stage = 'queue_contacts'
        for c in contacts:
            db.update('contacts', {
                'state': 'QUEUED',
                'detail': f'이미지 발송 JOB #{jid} 배정 완료',
            }, eq={'id': c['id'], 'user_id': user})

        stage = 'log'
        event(user, jid, 'INFO', 'JOB', f'이미지+버튼 발송 JOB 생성 / 체크 {len(checked_ids)}개 / 실제 작업 {len(effective_ids)}개 / 이번 확인 대상 {len(targets)}건')
        result = dict(job)
        result['assigned_count'] = len(targets)
        result['requested_account_count'] = len(checked_ids)
        result['selected_account_count'] = len(effective_ids)
        result['selected_account_ids'] = effective_ids
        result['excluded_zero_target_account_ids'] = excluded_zero_target_ids
        result['account_target_counts'] = target_counts
        result['reused'] = False
        return result
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e or '')[:700]
        if 'INSUFFICIENT_POINTS_FOR_JOB' in msg:
            raise HTTPException(409, '발송 포인트가 부족합니다.')
        raise HTTPException(500, f'IMAGE_JOB_CREATE_FAILED:{stage}:{msg or type(e).__name__}')
