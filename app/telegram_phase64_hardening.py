import asyncio
import random
import secrets

from telethon import Button, TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl import functions
from telethon.tl.types import InputPhoneContact

from . import db
from . import main
from . import contact_batch_import as cbi
from .security import dec
from .telegram import client_from_account, is_rate_error


_send_account_locks = {}
_import_account_locks = {}


def _lock(store, account_id):
    key = int(account_id)
    if key not in store:
        store[key] = asyncio.Lock()
    return store[key]


def _safe_error(e):
    return str(e or '')[:500]


async def _parallel_contact_import(user, bid, account_ids, per_account):
    accounts = cbi._ready_accounts(user, account_ids)
    contacts = cbi._available_contacts(user, bid)
    allocations = cbi._allocate(contacts, accounts, per_account)
    progress = cbi._progress_seed(accounts, allocations)
    assigned_total = cbi._progress_total(progress)
    save_lock = asyncio.Lock()
    halt = asyncio.Event()

    async def save(**extra):
        async with save_lock:
            processed = sum(int(v.get('processed') or 0) for v in progress.values())
            resolved = sum(int(v.get('resolved') or 0) for v in progress.values())
            failed = sum(int(v.get('failed') or 0) for v in progress.values())
            cbi._save_progress(user, bid, progress, processed, resolved, failed, **extra)

    if assigned_total <= 0:
        db.update('contact_batches', {
            'contact_import_status': 'COMPLETED',
            'contact_import_processed': 0,
            'contact_import_resolved': 0,
            'contact_import_failed': 0,
            'contact_import_account_ids': [int(x) for x in account_ids],
            'contact_import_per_account': per_account,
            'contact_import_started_at': main.now_iso(),
            'contact_import_completed_at': main.now_iso(),
            'contact_import_error': None,
            'contact_import_account_progress': progress,
        }, eq={'id': bid, 'user_id': user})
        cbi._import_tasks.pop(str(bid), None)
        return

    db.update('contact_batches', {
        'contact_import_status': 'RUNNING',
        'contact_import_processed': 0,
        'contact_import_resolved': 0,
        'contact_import_failed': 0,
        'contact_import_account_ids': [int(x) for x in account_ids],
        'contact_import_per_account': per_account,
        'contact_import_started_at': main.now_iso(),
        'contact_import_completed_at': None,
        'contact_import_error': None,
        'contact_import_account_progress': progress,
    }, eq={'id': bid, 'user_id': user})

    async def account_worker(account):
        aid = int(account['id'])
        key = str(aid)
        rows = allocations.get(aid, [])
        if not rows:
            return
        async with _lock(_import_account_locks, aid):
            client = None
            try:
                if halt.is_set():
                    progress[key]['status'] = 'PAUSED'
                    await save()
                    return
                progress[key]['status'] = 'RUNNING'
                await save()
                client = await client_from_account(account)
                await client.connect()
                me = await client.get_me()
                if not me:
                    raise RuntimeError(f'ACCOUNT_NOT_READY:{aid}')

                for start in range(0, len(rows), cbi.BATCH_SIZE):
                    if halt.is_set():
                        progress[key]['status'] = 'PAUSED'
                        await save()
                        return
                    chunk = rows[start:start + cbi.BATCH_SIZE]
                    request = []
                    mapping = {}
                    for item in chunk:
                        cid = random.randrange(1, 2**63)
                        while cid in mapping:
                            cid = random.randrange(1, 2**63)
                        mapping[cid] = item
                        request.append(InputPhoneContact(
                            client_id=cid,
                            phone=cbi._telegram_phone(item['phone']),
                            first_name=f'N-{str(item["id"])[:8]}',
                            last_name='',
                        ))
                    try:
                        result = await client(functions.contacts.ImportContactsRequest(request))
                    except Exception as e:
                        if is_rate_error(e):
                            halt.set()
                            progress[key]['status'] = 'PAUSED'
                            progress[key]['error'] = _safe_error(e)
                            await save(
                                contact_import_status='PAUSED',
                                contact_import_error=f'TELEGRAM_RATE_LIMIT: {_safe_error(e)}',
                            )
                            return
                        for item in chunk:
                            progress[key]['failed'] += 1
                            progress[key]['processed'] += 1
                            db.update('contacts', {
                                'assigned_account_id': aid,
                                'state': 'IMPORT_FAILED',
                                'detail': _safe_error(e),
                            }, eq={'id': item['id'], 'user_id': user})
                        await save()
                        continue

                    imported = {int(x.client_id): int(x.user_id) for x in (getattr(result, 'imported', None) or [])}
                    for cid, item in mapping.items():
                        progress[key]['processed'] += 1
                        uid = imported.get(int(cid))
                        if uid:
                            progress[key]['resolved'] += 1
                            db.update('contacts', {
                                'telegram_user_id': uid,
                                'assigned_account_id': aid,
                                'state': 'RESOLVED',
                                'detail': '연락처 추가 완료 / Telegram UID 확인',
                            }, eq={'id': item['id'], 'user_id': user})
                        else:
                            progress[key]['failed'] += 1
                            db.update('contacts', {
                                'telegram_user_id': None,
                                'assigned_account_id': aid,
                                'state': 'NOT_RESOLVED',
                                'detail': '연락처 추가 완료 / Telegram 사용자 확인 불가',
                            }, eq={'id': item['id'], 'user_id': user})
                    await save()
                    await asyncio.sleep(0)

                progress[key]['status'] = 'COMPLETED'
                await save()
            except Exception as e:
                progress[key]['status'] = 'FAILED'
                progress[key]['error'] = _safe_error(e)
                await save()
            finally:
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

    try:
        await asyncio.gather(*(account_worker(a) for a in accounts))
        if halt.is_set():
            for v in progress.values():
                if v.get('status') in ('RUNNING', 'WAITING'):
                    v['status'] = 'PAUSED'
            await save(contact_import_status='PAUSED')
        else:
            await save(contact_import_status='COMPLETED', contact_import_completed_at=main.now_iso())
    except Exception as e:
        for v in progress.values():
            if v.get('status') in ('RUNNING', 'WAITING'):
                v['status'] = 'FAILED'
                v['error'] = _safe_error(e)
        await save(contact_import_status='FAILED', contact_import_error=_safe_error(e))
    finally:
        cbi._import_tasks.pop(str(bid), None)


async def _start_inline_responder(job, first_account, allowed_target_ids):
    token = dec(job['bot_token_enc'])
    username = str(job['bot_username'] or '').lstrip('@')
    client = TelegramClient(StringSession(), int(first_account['api_id']), dec(first_account['api_hash_enc']))
    await client.start(bot_token=token)
    me = await client.get_me()
    if not me or str(getattr(me, 'username', '') or '').lower() != username.lower():
        await client.disconnect()
        raise RuntimeError('BOT_USERNAME_TOKEN_MISMATCH')

    @client.on(events.InlineQuery)
    async def inline_handler(event):
        q = str(event.text or '')
        prefix = f'npaydm:{job["id"]}:'
        if not q.startswith(prefix):
            return
        parts = q.split(':', 3)
        if len(parts) != 4:
            await event.answer([], cache_time=0, private=True)
            return
        target_part = parts[2]
        if target_part != 'probe':
            try:
                if int(target_part) not in allowed_target_ids:
                    await event.answer([], cache_time=0, private=True)
                    return
            except Exception:
                await event.answer([], cache_time=0, private=True)
                return
        buttons = [[Button.url(str(job['button_text'])[:64], str(job['button_url']))]]
        result = event.builder.article(
            title='N PAY 메시지',
            text=str(job['message_text'] or ''),
            buttons=buttons,
            link_preview=False,
        )
        await event.answer([result], cache_time=0, private=True)

    return client


async def _parallel_run_job(user, jid):
    job = db.one('jobs', user, eq={'id': jid})
    if not job:
        return
    accounts = {str(a['id']): a for a in db.rows('telegram_accounts', user, order=None)}
    stop = main._stops[jid]
    pause = main._pauses[jid]
    targets_all = db.rows('job_targets', user, eq={'job_id': jid}, order='created_at')
    groups = {}
    for t in targets_all:
        aid = str(t.get('assigned_account_id') or '')
        if aid:
            groups.setdefault(aid, []).append(t)

    history = db.rows('send_history', user, order=None)
    sent_phones = {str(x.get('phone') or '') for x in history if x.get('phone')}
    sent_uids = {str(x.get('telegram_user_id') or '') for x in history if x.get('telegram_user_id')}
    dedupe_lock = asyncio.Lock()
    inline_bot = None

    db.update('jobs', {'status': 'RUNNING', 'updated_at': main.now_iso()}, eq={'id': jid, 'user_id': user})
    main.event(user, jid, 'INFO', 'JOB', f'계정별 독립 병렬 발송 시작 / 계정 {len(groups)}개')

    try:
        allowed_ids = {int(t['id']) for t in targets_all}
        if job.get('button_text') and job.get('button_url'):
            first_account = next((accounts.get(aid) for aid in groups if accounts.get(aid)), None)
            if not first_account:
                raise RuntimeError('READY_ACCOUNT_NOT_FOUND')
            inline_bot = await _start_inline_responder(job, first_account, allowed_ids)

            probe = await client_from_account(first_account)
            try:
                await probe.connect()
                probe_results = await probe.inline_query(str(job['bot_username']).lstrip('@'), f'npaydm:{jid}:probe:{secrets.token_urlsafe(8)}')
                if not probe_results:
                    raise RuntimeError('INLINE_BOT_NOT_READY: BotFather Inline Mode를 확인해 주세요.')
            finally:
                try:
                    await probe.disconnect()
                except Exception:
                    pass
            main.event(user, jid, 'INFO', 'SOURCE', 'Inline URL 버튼 원본 준비 완료')

        async def account_sender(aid, rows):
            account = accounts.get(aid)
            if not account:
                return
            async with _lock(_send_account_locks, int(aid)):
                client = None
                try:
                    client = await client_from_account(account)
                    await client.connect()
                    me = await client.get_me()
                    if not me:
                        raise RuntimeError(f'ACCOUNT_NOT_READY:{aid}')
                    main.event(user, jid, 'INFO', 'ACCOUNT', f'계정 작업 시작 / 배정 {len(rows)}건', aid)

                    for t in rows:
                        if stop.is_set():
                            return
                        while pause.is_set() and not stop.is_set():
                            await asyncio.sleep(.3)
                        if stop.is_set():
                            return
                        if str(t.get('state') or '') != 'WAITING':
                            continue

                        tid = t['id']
                        uid = t.get('telegram_user_id')
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
                                if str(t.get('phone') or '') in sent_phones or str(uid) in sent_uids:
                                    db.update('job_targets', {
                                        'state': 'SKIPPED',
                                        'stage': '중복 제외',
                                        'updated_at': main.now_iso(),
                                    }, eq={'id': tid, 'user_id': user})
                                    main.event(user, jid, 'INFO', 'DEDUPE', f"{t['phone']} 기존 성공 이력으로 제외", aid)
                                    main.recalc_job(user, jid)
                                    continue

                        db.update('job_targets', {
                            'state': 'PROCESSING',
                            'stage': '발송 중',
                            'updated_at': main.now_iso(),
                        }, eq={'id': tid, 'user_id': user})

                        try:
                            peer = await client.get_input_entity(int(uid))
                            if job.get('button_text') and job.get('button_url'):
                                results = await client.inline_query(
                                    str(job['bot_username']).lstrip('@'),
                                    f'npaydm:{jid}:{tid}:{secrets.token_urlsafe(8)}',
                                )
                                if not results:
                                    raise RuntimeError('INLINE_BOT_NOT_READY: BotFather Inline Mode를 확인해 주세요.')
                                sent = await results[0].click(peer)
                            else:
                                sent = await client.send_message(peer, str(job['message_text'] or ''))
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
                                'phone': t['phone'],
                                'telegram_user_id': int(uid),
                                'account_id': int(aid),
                                'job_id': int(jid),
                                'message_id': mid,
                            })
                            async with dedupe_lock:
                                sent_phones.add(str(t.get('phone') or ''))
                                sent_uids.add(str(uid))
                            current = accounts.get(aid) or {}
                            new_count = int(current.get('sent_count') or 0) + 1
                            db.update('telegram_accounts', {'sent_count': new_count}, eq={'id': int(aid), 'user_id': user})
                            current['sent_count'] = new_count
                            main.event(user, jid, 'INFO', 'SEND', f"{t['phone']} 발송 완료", aid)
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

                            code = 'INLINE_BOT_NOT_READY' if 'INLINE_BOT_NOT_READY' in str(e) else 'SEND_FAILED'
                            db.update('job_targets', {
                                'state': 'FAILED',
                                'stage': '발송 실패',
                                'error_code': code,
                                'error_detail': _safe_error(e),
                                'updated_at': main.now_iso(),
                            }, eq={'id': tid, 'user_id': user})
                            main.event(user, jid, 'ERROR', 'SEND', f"{t['phone']} 발송 실패: {_safe_error(e)}", aid)
                            main.recalc_job(user, jid)

                        lo = max(0.0, float(job.get('delay_min') or 0))
                        hi = max(lo, float(job.get('delay_max') or lo))
                        if hi > 0:
                            await asyncio.sleep(random.uniform(lo, hi))

                    main.event(user, jid, 'INFO', 'ACCOUNT', '계정 배정 작업 완료', aid)
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
            main.event(user, jid, 'INFO', 'JOB', '모든 계정 배정 작업 완료')
    except Exception as e:
        db.update('jobs', {
            'status': 'PAUSED',
            'stop_reason': _safe_error(e),
            'updated_at': main.now_iso(),
        }, eq={'id': jid, 'user_id': user})
        main.event(user, jid, 'ERROR', 'JOB', _safe_error(e))
    finally:
        if inline_bot is not None:
            try:
                await inline_bot.disconnect()
            except Exception:
                pass
        main._tasks.pop(jid, None)
        main._stops.pop(jid, None)
        main._pauses.pop(jid, None)


cbi._run_import = _parallel_contact_import
main.run_job = _parallel_run_job
