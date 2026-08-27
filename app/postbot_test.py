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
    target: str


@app.post('/v1/telegram/postbot-test')
async def postbot_test(p: PostBotTest, user=Depends(auth)):
    code = str(p.post_code or '').strip()
    target = str(p.target or '').strip()
    if not code:
        raise HTTPException(400, 'POSTBOT_CODE_REQUIRED')
    if not target:
        raise HTTPException(400, 'TARGET_REQUIRED')

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

        if target.lower() in ('me', 'saved', 'saved messages'):
            peer = await client.get_input_entity('me')
        else:
            t = target
            if t.startswith('@'):
                t = t[1:]
            try:
                peer = await client.get_input_entity(int(t)) if t.lstrip('-').isdigit() else await client.get_input_entity(t)
            except Exception:
                raise HTTPException(404, 'TARGET_NOT_FOUND')

        sent = await results[0].click(peer)
        message_id = int(getattr(sent, 'id', 0) or 0) or None
        return {
            'ok': True,
            'message_id': message_id,
            'result_count': len(results),
            'account_id': int(p.account_id),
            'target': target,
            'post_code': code,
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
