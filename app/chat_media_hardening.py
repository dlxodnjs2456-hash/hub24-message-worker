import base64

from fastapi import Depends, HTTPException

from .main import app, auth
from . import db
from .telegram import client_from_account


@app.get('/v1/accounts/{aid}/dialogs/{peer_id}/messages')
async def dialog_messages_with_media(aid: str, peer_id: int, limit: int = 50, user=Depends(auth)):
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
        items = []

        for m in messages:
            media_data_url = None
            has_photo = bool(getattr(m, 'photo', None))
            if has_photo:
                try:
                    # A Telegram photo thumbnail is enough for the web chat viewer.
                    # Keep it in-memory only; nothing is written to disk or Supabase.
                    raw = await c.download_media(m, file=bytes, thumb=1)
                    if raw:
                        media_data_url = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')
                except Exception:
                    media_data_url = None

            items.append({
                'id': int(m.id),
                'date': m.date.isoformat() if m.date else None,
                'out': bool(m.out),
                'text': m.message or '',
                'has_photo': has_photo,
                'media_data_url': media_data_url,
            })

        return {'items': items}
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass
