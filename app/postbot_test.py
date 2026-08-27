import asyncio

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .main import app, auth
from . import db
from .telegram import client_from_account, is_rate_error


def q(table):
    return db.sb.table(table)


class PostBotTest(BaseModel):
    account_id: int
    post_code: str
    targets: list[str]


@app.post('/v1/telegram/postbot-test')
async def postbot_test(p: PostBotTest, user=Depends(auth)):
    code = str(p.post_code or '').strip()
    targets = [str(x or '').strip() for x in (p.targets or []) if str(x or '').strip()]
    if not code:
        raise HTTPException(400, 'POSTBOT_CODE_REQUIRED')
    if len(targets) != 2:
        raise HTTPException(400, 'POSTBOT_TEST_REQUIRES_EXACTLY_2_TARGETS')
    if targets[0].lower() == targets[1].lower():
        raise HTTPException(400, 'TARGETS_MUST_BE_DIFFERENT')

    rows = q('telegram_accounts').select('*').eq('id', int(p.account_id)).eq('user_id', user).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, 'ACCOUNT_NOT_FOUND')
    account = rows[0]
    if str(account.get('status') or '').upper() not in ('READY', 'CONNECTED'):
        raise HTTPException(409, 'ACCOUNT_NOT_READY')

    client = None
    try:
        client = await client_from_account(account)
        await client.connect()
        me = await client.get_me()
        if not me:
            raise HTTPException(409, 'ACCOUNT_NOT_READY')

        bot = await client.get_input_entity('PostBot')
        results = await client.inline_query(bot, code)
        if not results:
            raise HTTPException(409, 'POSTBOT_RESULT_NOT_FOUND')

        async def resolve_target(target: str):
            if target.lower() in ('me', 'saved', 'saved messages'):
                return await client.get_input_entity('me')
            t = target[1:] if target.startswith('@') else target
            try:
                return await client.get_input_entity(int(t)) if t.lstrip('-').isdigit() else await client.get_input_entity(t)
            except Exception:
                raise RuntimeError(f'TARGET_NOT_FOUND:{target}')

        peers = await asyncio.gather(*(resolve_target(t) for t in targets))

        async def send_one(target: str, peer):
            try:
                sent = await results[0].click(peer)
                return {
                    'target': target,
                    'ok': True,
                    'message_id': int(getattr(sent, 'id', 0) or 0) or None,
                    'error': None,
                }
            except Exception as e:
                msg = str(e or '')[:500]
                if is_rate_error(e):
                    return {'target': target, 'ok': False, 'message_id': None, 'error': f'TELEGRAM_RATE_LIMIT:{msg}'}
                return {'target': target, 'ok': False, 'message_id': None, 'error': msg or 'POSTBOT_TEST_FAILED'}

        sent_results = await asyncio.gather(*(send_one(t, peer) for t, peer in zip(targets, peers)))
        success_count = sum(1 for x in sent_results if x['ok'])
        return {
            'ok': success_count == 2,
            'success_count': success_count,
            'failed_count': 2 - success_count,
            'result_count': len(results),
            'account_id': int(p.account_id),
            'post_code': code,
            'items': sent_results,
        }
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e or '')[:500]
        if is_rate_error(e):
            raise HTTPException(429, f'TELEGRAM_RATE_LIMIT:{msg}')
        raise HTTPException(400, msg or 'POSTBOT_TEST_FAILED')
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
