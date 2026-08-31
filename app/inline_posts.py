import re
import secrets
from urllib.parse import quote

import httpx
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from . import db
from .main import app, auth, now_iso
from .security import dec, enc
from .settings import settings


CODE_RE = re.compile(r'^[A-Z0-9_-]{2,40}$')


class InlineBotRegister(BaseModel):
    bot_token: str


def _safe_bot(row):
    if not row:
        return None
    return {
        'id': row.get('id'),
        'bot_id': row.get('bot_id'),
        'bot_username': row.get('bot_username'),
        'owner_chat_connected': bool(row.get('owner_chat_id')),
        'is_active': bool(row.get('is_active')),
        'created_at': row.get('created_at'),
        'updated_at': row.get('updated_at'),
    }


def _active_bot(user):
    rows = db.rows('npay_inline_bots', user, eq={'is_active': True}, order='created_at', desc=True, limit=1)
    return rows[0] if rows else None


async def _bot_api(token, method, *, data=None, files=None):
    url = f'https://api.telegram.org/bot{token}/{method}'
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, data=data, files=files)
    try:
        body = r.json()
    except Exception:
        body = {'ok': False, 'description': r.text[:500]}
    if not r.is_success or not body.get('ok'):
        raise RuntimeError(str(body.get('description') or f'TELEGRAM_HTTP_{r.status_code}')[:500])
    return body.get('result')


@app.get('/v1/inline-bot')
def get_inline_bot(user=Depends(auth)):
    bot = _active_bot(user)
    if not bot:
        return {'bot': None, 'pairing_url': None}
    pairing_url = None
    if not bot.get('owner_chat_id'):
        pairing_url = f"https://t.me/{bot['bot_username']}?start={quote(str(bot.get('pairing_code') or ''))}"
    return {'bot': _safe_bot(bot), 'pairing_url': pairing_url}


@app.post('/v1/inline-bot')
async def register_inline_bot(p: InlineBotRegister, user=Depends(auth)):
    token = str(p.bot_token or '').strip()
    if not token or ':' not in token:
        raise HTTPException(400, '유효한 Bot Token을 입력하세요.')
    try:
        me = await _bot_api(token, 'getMe')
    except Exception as e:
        raise HTTPException(400, f'Bot Token 확인 실패: {str(e)[:300]}')
    username = str((me or {}).get('username') or '').strip().lstrip('@')
    bot_id = int((me or {}).get('id') or 0)
    if not username or not bot_id:
        raise HTTPException(400, 'Bot username을 확인할 수 없습니다.')
    if not bool((me or {}).get('supports_inline_queries')):
        raise HTTPException(409, f'@{username} 봇의 Inline Mode가 꺼져 있습니다. BotFather에서 이 봇을 선택한 뒤 /setinline을 실행하고 다시 등록하세요.')

    current = _active_bot(user)
    replacing = bool(current and int(current.get('bot_id') or 0) != bot_id)
    if current:
        db.update('npay_inline_bots', {'is_active': False, 'updated_at': now_iso()}, eq={'id': current['id'], 'user_id': user})

    existing_rows = db.rows('npay_inline_bots', user, eq={'bot_id': bot_id}, order='created_at', desc=True, limit=1)
    pairing_code = secrets.token_urlsafe(12).replace('-', '').replace('_', '')[:20]
    if existing_rows:
        bot = existing_rows[0]
        db.update('npay_inline_bots', {
            'bot_username': username,
            'bot_token_enc': enc(token),
            'pairing_code': pairing_code,
            'owner_chat_id': None,
            'is_active': True,
            'updated_at': now_iso(),
        }, eq={'id': bot['id'], 'user_id': user})
        bot = db.one('npay_inline_bots', user, eq={'id': bot['id']})
    else:
        bot = db.insert('npay_inline_bots', {
            'user_id': user,
            'bot_id': bot_id,
            'bot_username': username,
            'bot_token_enc': enc(token),
            'pairing_code': pairing_code,
            'is_active': True,
        })

    public_base = str(settings.public_base_url or '').rstrip('/')
    if not public_base:
        raise HTTPException(500, 'HUB24_PUBLIC_BASE_URL_NOT_SET')
    webhook_url = f"{public_base}/v1/inline-bot/webhook/{bot['webhook_key']}"
    try:
        await _bot_api(token, 'setWebhook', data={
            'url': webhook_url,
            'allowed_updates': '["message","inline_query"]',
            'drop_pending_updates': 'true',
        })
    except Exception as e:
        db.update('npay_inline_bots', {'is_active': False, 'updated_at': now_iso()}, eq={'id': bot['id'], 'user_id': user})
        if current:
            db.update('npay_inline_bots', {'is_active': True, 'updated_at': now_iso()}, eq={'id': current['id'], 'user_id': user})
        raise HTTPException(502, f'Webhook 연결 실패: {str(e)[:300]}')

    # A Telegram cached-photo file_id is bot-specific. Keep post rows/codes,
    # but require image re-upload when a different bot replaces the old bot.
    if replacing:
        for post in db.rows('npay_inline_posts', user, order=None):
            db.update('npay_inline_posts', {'image_file_id': None, 'updated_at': now_iso()}, eq={'id': post['id'], 'user_id': user})

    pairing_url = f'https://t.me/{username}?start={quote(pairing_code)}'
    return {'bot': _safe_bot(bot), 'pairing_url': pairing_url, 'posts_require_image_refresh': replacing}


@app.get('/v1/inline-posts')
def list_inline_posts(user=Depends(auth)):
    items = db.rows('npay_inline_posts', user, order='created_at', desc=True)
    return {'items': items}


@app.delete('/v1/inline-posts/{post_id}')
def delete_inline_post(post_id: int, user=Depends(auth)):
    post = db.one('npay_inline_posts', user, eq={'id': post_id})
    if not post:
        raise HTTPException(404, '게시물을 찾을 수 없습니다.')
    db.delete('npay_inline_posts', eq={'id': post_id, 'user_id': user})
    return {'ok': True}


@app.post('/v1/inline-posts')
async def create_inline_post(
    code: str = Form(...),
    caption: str = Form(''),
    button_text: str = Form(''),
    button_url: str = Form(''),
    image: UploadFile = File(...),
    user=Depends(auth),
):
    normalized = str(code or '').strip().upper()
    if not CODE_RE.fullmatch(normalized):
        raise HTTPException(400, '게시물 코드는 영문 대문자/숫자/_/- 조합 2~40자로 입력하세요.')
    if db.rows('npay_inline_posts', user, eq={'code': normalized}, order=None, limit=1):
        raise HTTPException(409, '이미 사용 중인 게시물 코드입니다.')
    if bool(button_text.strip()) != bool(button_url.strip()):
        raise HTTPException(400, '버튼명과 버튼 URL은 함께 입력하세요.')
    if button_url and not re.match(r'^https?://', button_url.strip(), re.I):
        raise HTTPException(400, '버튼 URL은 http:// 또는 https://로 시작해야 합니다.')

    bot = _active_bot(user)
    if not bot:
        raise HTTPException(409, '먼저 개인 Inline Bot을 등록하세요.')
    if not bot.get('owner_chat_id'):
        raise HTTPException(409, '개인 Inline Bot 연결을 완료하세요. 봇 연결 버튼을 누르고 Telegram에서 시작하세요.')
    raw = await image.read()
    if not raw:
        raise HTTPException(400, '이미지 파일이 비어 있습니다.')
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, '이미지는 10MB 이하만 업로드할 수 있습니다.')
    token = dec(bot['bot_token_enc'])
    try:
        sent = await _bot_api(token, 'sendPhoto', data={'chat_id': str(bot['owner_chat_id'])}, files={
            'photo': (image.filename or 'image.jpg', raw, image.content_type or 'image/jpeg')
        })
        photos = (sent or {}).get('photo') or []
        if not photos:
            raise RuntimeError('PHOTO_FILE_ID_NOT_RETURNED')
        file_id = str(photos[-1].get('file_id') or '')
        if not file_id:
            raise RuntimeError('PHOTO_FILE_ID_NOT_RETURNED')
        try:
            await _bot_api(token, 'deleteMessage', data={'chat_id': str(bot['owner_chat_id']), 'message_id': str(sent.get('message_id'))})
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(502, f'Telegram 이미지 등록 실패: {str(e)[:300]}')

    post = db.insert('npay_inline_posts', {
        'user_id': user,
        'code': normalized,
        'image_file_id': file_id,
        'caption': str(caption or '')[:1024],
        'button_text': str(button_text or '')[:64],
        'button_url': str(button_url or '')[:2048],
        'is_active': True,
    })
    return post


@app.post('/v1/inline-bot/webhook/{webhook_key}')
async def inline_bot_webhook(webhook_key: str, request: Request):
    rows = db.sb.table('npay_inline_bots').select('*').eq('webhook_key', webhook_key).eq('is_active', True).limit(1).execute().data or []
    if not rows:
        return {'ok': True}
    bot = rows[0]
    token = dec(bot['bot_token_enc'])
    update = await request.json()

    message = update.get('message') or {}
    text = str(message.get('text') or '').strip()
    if text.startswith('/start'):
        parts = text.split(maxsplit=1)
        supplied = parts[1].strip() if len(parts) > 1 else ''
        if supplied and secrets.compare_digest(supplied, str(bot.get('pairing_code') or '')):
            chat_id = (message.get('chat') or {}).get('id')
            if chat_id:
                db.sb.table('npay_inline_bots').update({'owner_chat_id': int(chat_id), 'updated_at': now_iso()}).eq('id', bot['id']).execute()
                try:
                    await _bot_api(token, 'sendMessage', data={'chat_id': str(chat_id), 'text': 'N PAY Inline Bot 연결이 완료되었습니다.'})
                except Exception:
                    pass
        return {'ok': True}

    iq = update.get('inline_query') or {}
    if iq.get('id'):
        code = str(iq.get('query') or '').strip().upper()
        posts = db.sb.table('npay_inline_posts').select('*').eq('user_id', bot['user_id']).eq('code', code).eq('is_active', True).limit(1).execute().data or []
        results = []
        if posts:
            post = posts[0]
            file_id = str(post.get('image_file_id') or '')
            if file_id:
                result = {
                    'type': 'photo',
                    'id': str(post['id']),
                    'photo_file_id': file_id,
                    'caption': str(post.get('caption') or '')[:1024],
                }
                if post.get('button_text') and post.get('button_url'):
                    result['reply_markup'] = {'inline_keyboard': [[{'text': post['button_text'], 'url': post['button_url']}]]}
                results = [result]
        try:
            await _bot_api(token, 'answerInlineQuery', data={
                'inline_query_id': str(iq['id']),
                'results': __import__('json').dumps(results, ensure_ascii=False),
                'cache_time': '0',
                'is_personal': 'true',
            })
        except Exception:
            pass
    return {'ok': True}
