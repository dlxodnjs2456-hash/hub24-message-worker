import asyncio
import random
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from telethon.tl import functions
from telethon.tl.types import InputPhoneContact

from .main import app, auth
from . import db
from .telegram import client_from_account, is_rate_error


def q(table):
    return db.sb.table(table)


def _phone(value: str):
    digits = re.sub(r'\D', '', str(value or ''))
    if digits.startswith('82'):
        digits = '0' + digits[2:]
    if len(digits) == 11 and digits.startswith('010'):
        return '+82' + digits[1:]
    return None


def _display_phone(value: str):
    digits = re.sub(r'\D', '', str(value or ''))
    if digits.startswith('82'):
        digits = '0' + digits[2:]
    if len(digits) == 11 and digits.startswith('010'):
        return f'{digits[:3]}-{digits[3:7]}-{digits[7:]}'
    return str(value or '')


class PostBotTest(BaseModel):
    account_id: int
    post_code: str
    phones: list[str]


@app.post('/v1/telegram/postbot-test')
async def postbot_test(p: PostBotTest, user=Depends(auth)):
    code = str(p.post_code or '').strip()
    raw_phones = [str(x or '').strip() for x in (p.phones or [])]
    if not code:
        raise HTTPException(400, 'POSTBOT_CODE_REQUIRED')
    if len(raw_phones) != 3 or any(not x for x in raw_phones):
        raise HTTPException(400, 'POSTBOT_TEST_REQUIRES_EXACTLY_3_PHONES')

    normalized = [_phone(x) for x in raw_phones]
    if any(not x for x in normalized):
        raise HTTPException(400, 'INVALID_KR_PHONE_NUMBER')
    if len(set(normalized)) != 3:
        raise HTTPException(400, 'PHONE_NUMBERS_MUST_BE_DIFFERENT')

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

        # Match the real delivery flow: add the three phone numbers as contacts,
        # resolve Telegram user IDs, then send the same PostBot inline result.
        request = []
        cid_to_index = {}
        for index, phone in enumerate(normalized):
            cid = random.randrange(1, 2**63)
            while cid in cid_to_index:
                cid = random.randrange(1, 2**63)
            cid_to_index[cid] = index
            request.append(InputPhoneContact(
                client_id=cid,
                phone=phone,
                first_name=f'NPay-Test-{index + 1}',
                last_name='',
            ))

        try:
            imported = await client(functions.contacts.ImportContactsRequest(request))
        except Exception as e:
            msg = str(e or '')[:500]
            if is_rate_error(e):
                raise HTTPException(429, f'TELEGRAM_RATE_LIMIT:{msg}')
            raise HTTPException(400, f'CONTACT_IMPORT_FAILED:{msg}')

        resolved = {}
        for item in (getattr(imported, 'imported', None) or []):
            idx = cid_to_index.get(int(item.client_id))
            if idx is not None:
                resolved[idx] = int(item.user_id)

        bot = await client.get_input_entity('PostBot')
        results = await client.inline_query(bot, code)
        if not results:
            raise HTTPException(409, 'POSTBOT_RESULT_NOT_FOUND')

        async def send_one(index: int):
            display = _display_phone(raw_phones[index])
            uid = resolved.get(index)
            if not uid:
                return {
                    'phone': display,
                    'telegram_user_id': None,
                    'contact_resolved': False,
                    'ok': False,
                    'message_id': None,
                    'error': 'TELEGRAM_USER_NOT_RESOLVED',
                }
            try:
                peer = await client.get_input_entity(uid)
                sent = await results[0].click(peer)
                return {
                    'phone': display,
                    'telegram_user_id': uid,
                    'contact_resolved': True,
                    'ok': True,
                    'message_id': int(getattr(sent, 'id', 0) or 0) or None,
                    'error': None,
                }
            except Exception as e:
                msg = str(e or '')[:500]
                if is_rate_error(e):
                    return {
                        'phone': display,
                        'telegram_user_id': uid,
                        'contact_resolved': True,
                        'ok': False,
                        'message_id': None,
                        'error': f'TELEGRAM_RATE_LIMIT:{msg}',
                    }
                return {
                    'phone': display,
                    'telegram_user_id': uid,
                    'contact_resolved': True,
                    'ok': False,
                    'message_id': None,
                    'error': msg or 'POSTBOT_TEST_FAILED',
                }

        sent_results = await asyncio.gather(*(send_one(i) for i in range(3)))
        success_count = sum(1 for x in sent_results if x['ok'])
        resolved_count = sum(1 for x in sent_results if x['contact_resolved'])
        return {
            'ok': success_count == 3,
            'success_count': success_count,
            'failed_count': 3 - success_count,
            'resolved_count': resolved_count,
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
