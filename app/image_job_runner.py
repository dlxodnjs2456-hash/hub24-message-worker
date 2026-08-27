import asyncio
import random

from . import db
from . import main
from . import telegram_phase64_hardening as phase64
from .telegram import client_from_account, is_rate_error


_previous_run_job = main.run_job


def _safe_error(e):
    return str(e or '')[:500]


async def _resolve_postbot(client):
    """Resolve the inline bot once per Telegram client.

    Some sessions fail when a bare username string is passed directly to
    inline_query(). Resolve the bot entity first and use the resolved entity
    for all inline queries made by that account.
    """
    last_error = None
    for username in ('@PostBot', '@postbot', '@post_bot'):
        try:
            entity = await client.get_entity(username)
            if entity:
                return entity
        except Exception as e:
            last_error = e
    raise RuntimeError(f'POSTBOT_USERNAME_NOT_RESOLVED:{_safe_error(last_error)}')


async def _run_image_job(user, jid):
    job = db.one('jobs', user, eq={'id': jid})
    if not job:
        return

    accounts = {str(a['id']): a for a in db.rows('telegram_accounts', user, order=None)}
    stop = main._stops[jid]
    pause = main._pauses[jid]
    targets_all = db.rows('job_targets', user, eq={'job_id': jid}, order='created_at')
    groups = {}
    for target in targets_all:
        aid = str(target.get('assigned_account_id') or '')
        if aid:
            groups.setdefault(aid, []).append(target)

    history = db.rows('send_history', user, order=None)
    sent_phones = {str(x.get('phone') or '') for x in history if x.get('phone')}
    sent_uids = {str(x.get('telegram_user_id') or '') for x in history if x.get('telegram_user_id')}
    dedupe_lock = asyncio.Lock()
    post_code = str(job.get('message_text') or '').strip()

    db.update('jobs', {'status': 'RUNNING', 'updated_at': main.now_iso()}, eq={'id': jid, 'user_id': user})
    main.event(user, jid, 'INFO', 'JOB', f'이미지+버튼 병렬 발송 시작 / 계정 {len(groups)}개')

    try:
        first_account = next((accounts.get(aid) for aid in groups if accounts.get(aid)), None)
        if not first_account:
            raise RuntimeError('READY_ACCOUNT_NOT_FOUND')
        probe = await client_from_account(first_account)
        try:
            await probe.connect()
            postbot = await _resolve_postbot(probe)
            probe_results = await probe.inline_query(postbot, post_code)
            if not probe_results:
                raise RuntimeError('POSTBOT_RESULT_NOT_FOUND')
        finally:
            try:
                await probe.disconnect()
            except Exception:
                pass
        main.event(user, jid, 'INFO', 'SOURCE', 'PostBot 이미지+버튼 원본 확인 완료')

        async def account_sender(aid, rows):
            account = accounts.get(aid)
            if not account:
                return
            async with phase64._lock(phase64._send_account_locks, int(aid)):
                client = None
                try:
                    client = await client_from_account(account)
                    await client.connect()
                    me = await client.get_me()
                    if not me:
                        raise RuntimeError(f'ACCOUNT_NOT_READY:{aid}')
                    postbot = await _resolve_postbot(client)
                    main.event(user, jid, 'INFO', 'ACCOUNT', f'이미지 발송 계정 시작 / 배정 {len(rows)}건', aid)

                    for target in rows:
                        if stop.is_set():
                            return
                        while pause.is_set() and not stop.is_set():
                            await asyncio.sleep(.3)
                        if stop.is_set():
                            return
                        if str(target.get('state') or '') != 'WAITING':
                            continue

                        tid = target['id']
                        uid = target.get('telegram_user_id')
                        if not uid:
                            db.update('job_targets', {
                                'state': 'FAILED',
                                'stage': 'UID 없음',
                                'error_code': 'TELEGRAM_UID_MISSING',
                                'error_detail': '연락처 추가 단계에서 Telegram UID가 확인되지 않았습니다.',
                                'updated_at': main.now_iso(),
                            }, eq={'id': tid, 'user_id': user})
                            main.recalc_job(user, jid)
                            continue

                        if job.get('global_dedupe'):
                            async with dedupe_lock:
                                if str(target.get('phone') or '') in sent_phones or str(uid) in sent_uids:
                                    db.update('job_targets', {
                                        'state': 'SKIPPED',
                                        'stage': '중복 제외',
                                        'updated_at': main.now_iso(),
                                    }, eq={'id': tid, 'user_id': user})
                                    main.event(user, jid, 'INFO', 'DEDUPE', f"{target['phone']} 기존 성공 이력으로 제외", aid)
                                    main.recalc_job(user, jid)
                                    continue

                        db.update('job_targets', {
                            'state': 'PROCESSING',
                            'stage': '이미지+버튼 발송 중',
                            'updated_at': main.now_iso(),
                        }, eq={'id': tid, 'user_id': user})

                        try:
                            peer = await client.get_input_entity(int(uid))
                            results = await client.inline_query(postbot, post_code)
                            if not results:
                                raise RuntimeError('POSTBOT_RESULT_NOT_FOUND')
                            sent = await results[0].click(peer)
                            mid = int(getattr(sent, 'id', 0) or 0) or None

                            db.update('job_targets', {
                                'state': 'SENT',
                                'stage': '완료',
                                'message_id': mid,
                                'error_code': None,
                                'error_detail': None,
                                'updated_at': main.now_iso(),
                            }, eq={'id': tid, 'user_id': user})
                            db.insert('send_history', {
                                'user_id': user,
                                'phone': target['phone'],
                                'telegram_user_id': int(uid),
                                'account_id': int(aid),
                                'job_id': int(jid),
                                'message_id': mid,
                            })
                            async with dedupe_lock:
                                sent_phones.add(str(target.get('phone') or ''))
                                sent_uids.add(str(uid))
                            current = accounts.get(aid) or {}
                            new_count = int(current.get('sent_count') or 0) + 1
                            db.update('telegram_accounts', {'sent_count': new_count}, eq={'id': int(aid), 'user_id': user})
                            current['sent_count'] = new_count
                            main.event(user, jid, 'INFO', 'SEND', f"{target['phone']} 이미지+버튼 발송 완료", aid)
                            main.recalc_job(user, jid)
                        except Exception as e:
                            if is_rate_error(e):
                                db.update('job_targets', {
                                    'state': 'WAITING',
                                    'stage': 'Telegram 제한 감지 / 운영자 확인 후 재개',
                                    'error_code': 'TELEGRAM_RATE_LIMIT',
                                    'error_detail': _safe_error(e),
                                    'updated_at': main.now_iso(),
                                }, eq={'id': tid, 'user_id': user})
                                pause.set()
                                db.update('jobs', {
                                    'status': 'PAUSED',
                                    'stop_reason': f'TELEGRAM_RATE_LIMIT account={aid}',
                                    'updated_at': main.now_iso(),
                                }, eq={'id': jid, 'user_id': user})
                                main.event(user, jid, 'WARN', 'RATE_LIMIT', f'계정 {aid} 제한 감지 / 전체 JOB 일시정지 / 운영자 확인 필요', aid)
                                while pause.is_set() and not stop.is_set():
                                    await asyncio.sleep(.5)
                                if stop.is_set():
                                    return
                                continue

                            text = str(e)
                            if 'POSTBOT_RESULT_NOT_FOUND' in text:
                                code = 'POSTBOT_RESULT_NOT_FOUND'
                            elif 'POSTBOT_USERNAME_NOT_RESOLVED' in text or 'No user has' in text:
                                code = 'POSTBOT_USERNAME_NOT_RESOLVED'
                            else:
                                code = 'SEND_FAILED'
                            db.update('job_targets', {
                                'state': 'FAILED',
                                'stage': '이미지 발송 실패',
                                'error_code': code,
                                'error_detail': _safe_error(e),
                                'updated_at': main.now_iso(),
                            }, eq={'id': tid, 'user_id': user})
                            main.event(user, jid, 'ERROR', 'SEND', f"{target['phone']} 이미지 발송 실패: {_safe_error(e)}", aid)
                            main.recalc_job(user, jid)

                        lo = max(0.0, float(job.get('delay_min') or 0))
                        hi = max(lo, float(job.get('delay_max') or lo))
                        if hi > 0:
                            await asyncio.sleep(random.uniform(lo, hi))

                    main.event(user, jid, 'INFO', 'ACCOUNT', '계정 이미지 발송 배정 완료', aid)
                finally:
                    if client is not None:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass

        await asyncio.gather(*(account_sender(aid, rows) for aid, rows in groups.items()))
        main.recalc_job(user, jid)
        if stop.is_set():
            db.update('jobs', {'status': 'STOPPED', 'updated_at': main.now_iso()}, eq={'id': jid, 'user_id': user})
        elif pause.is_set():
            db.update('jobs', {'status': 'PAUSED', 'updated_at': main.now_iso()}, eq={'id': jid, 'user_id': user})
        else:
            db.update('jobs', {'status': 'COMPLETED', 'updated_at': main.now_iso()}, eq={'id': jid, 'user_id': user})
            main.event(user, jid, 'INFO', 'JOB', '이미지+버튼 발송 작업 완료')
    except Exception as e:
        db.update('jobs', {
            'status': 'PAUSED',
            'stop_reason': _safe_error(e),
            'updated_at': main.now_iso(),
        }, eq={'id': jid, 'user_id': user})
        main.event(user, jid, 'ERROR', 'JOB', _safe_error(e))
    finally:
        main._tasks.pop(jid, None)
        main._stops.pop(jid, None)
        main._pauses.pop(jid, None)


async def run_job(user, jid):
    job = db.one('jobs', user, eq={'id': jid})
    if job and str(job.get('operation_mode') or '') == 'IMAGE_POSTBOT':
        return await _run_image_job(user, jid)
    return await _previous_run_job(user, jid)


main.run_job = run_job
