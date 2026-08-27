from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from .main import app, auth, event
from . import db
from .security import enc


class ImageJobCreate(BaseModel):
    batch_id: int
    post_code: str
    account_ids: list[int] = Field(default_factory=list)
    contacts_per_account: int = Field(default=50, ge=1, le=1000)
    delay_min: float = 2
    delay_max: float = 5
    global_dedupe: bool = True


@app.post('/v1/image-jobs')
def create_image_job(p: ImageJobCreate, user=Depends(auth)):
    code = str(p.post_code or '').strip()
    if not code:
        raise HTTPException(400, 'POSTBOT_CODE_REQUIRED')

    batch = db.one('contact_batches', user, eq={'id': p.batch_id})
    if not batch:
        raise HTTPException(404, 'batch not found')
    if str(batch.get('contact_import_status') or '') != 'COMPLETED':
        raise HTTPException(409, '먼저 연락처 추가 작업을 완료하세요.')

    # The checkbox state at the exact moment Start is pressed is authoritative.
    # Never fall back to accounts used by an earlier contact-import operation.
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

    checked_set = set(checked_ids)

    # Only successfully resolved contacts assigned to a currently checked
    # account are eligible. A checked account with zero resolved targets is
    # automatically excluded from the actual job rather than creating a
    # misleading empty worker group.
    contacts = db.rows('contacts', user, eq={'batch_id': p.batch_id, 'state': 'RESOLVED'}, order='created_at')
    contacts = [
        c for c in contacts
        if c.get('telegram_user_id') and int(c.get('assigned_account_id') or 0) in checked_set
    ]
    if not contacts:
        raise HTTPException(400, '체크한 계정에 발송 가능한 연락처가 없습니다. 연락처 확인 성공 건이 있는 계정을 선택하세요.')

    target_counts = {}
    for c in contacts:
        aid = int(c['assigned_account_id'])
        target_counts[aid] = target_counts.get(aid, 0) + 1

    effective_ids = [aid for aid in checked_ids if int(target_counts.get(aid, 0)) > 0]
    effective_set = set(effective_ids)
    excluded_zero_target_ids = [aid for aid in checked_ids if aid not in effective_set]
    if not effective_ids:
        raise HTTPException(400, '체크한 계정 중 연락처 확인 성공 대상이 있는 계정이 없습니다.')

    # If the browser lost the response after a successful create, reuse only a
    # pending image job whose actual worker-account set is exactly identical.
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

    required_points = len(contacts) * 15
    wallet = db.one('point_wallets', user, eq={'user_id': user}) or {}
    available_points = int(wallet.get('available_balance') or 0)
    if available_points < required_points:
        possible = available_points // 15
        raise HTTPException(
            409,
            f'발송 포인트가 부족합니다. 필요 {required_points:,}P / 보유 {available_points:,}P / 현재 최대 {possible:,}건 발송 가능'
        )

    try:
        job = db.insert('jobs', {
            'user_id': user,
            'batch_id': p.batch_id,
            'status': 'WAITING',
            'operation_mode': 'IMAGE_POSTBOT',
            'message_text': code,
            'button_text': '',
            'button_url': '',
            'bot_username': 'PostBot',
            'bot_token_enc': enc(''),
            'delay_min': p.delay_min,
            'delay_max': p.delay_max,
            'global_dedupe': p.global_dedupe,
            'total_count': len(contacts),
            'pending_count': len(contacts),
            'selected_account_ids': effective_ids,
            'contacts_per_account': int(batch.get('contact_import_per_account') or p.contacts_per_account or 50),
            'source_batch_total': int(batch.get('total_count') or len(contacts)),
        })
    except Exception as e:
        msg = str(e)
        if 'INSUFFICIENT_POINTS_FOR_JOB' in msg:
            raise HTTPException(409, f'발송 포인트가 부족합니다. 필요 {required_points:,}P / 보유 {available_points:,}P')
        raise

    jid = job['id']
    targets = []
    for c in contacts:
        targets.append({
            'user_id': user,
            'job_id': jid,
            'contact_id': c['id'],
            'phone': c['phone'],
            'telegram_user_id': c.get('telegram_user_id'),
            'assigned_account_id': int(c['assigned_account_id']),
            'state': 'WAITING',
            'stage': '이미지 발송 대기',
        })
    db.insert_many('job_targets', targets)
    for c in contacts:
        db.update('contacts', {
            'state': 'QUEUED',
            'detail': f'이미지 발송 JOB #{jid} 배정 완료',
        }, eq={'id': c['id'], 'user_id': user})

    event(user, jid, 'INFO', 'JOB', f'이미지+버튼 발송 JOB 생성 / 체크 {len(checked_ids)}개 / 실제 작업 {len(effective_ids)}개 / 대상 {len(targets)}건')
    result = dict(job)
    result['assigned_count'] = len(targets)
    result['requested_account_count'] = len(checked_ids)
    result['selected_account_count'] = len(effective_ids)
    result['selected_account_ids'] = effective_ids
    result['excluded_zero_target_account_ids'] = excluded_zero_target_ids
    result['account_target_counts'] = target_counts
    result['reused'] = False
    return result
