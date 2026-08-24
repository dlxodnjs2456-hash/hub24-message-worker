import asyncio,re
from pathlib import Path
from urllib.parse import urlparse
import httpx
from telethon import TelegramClient
from telethon.errors import FloodWaitError,PeerFloodError
from .security import dec
from .settings import settings

def norm_phone(v:str)->str:
    d=re.sub(r'\D','',str(v or ''))
    if d.startswith('82'):d='0'+d[2:]
    if d.startswith('10'):d='0'+d
    return d

def session_file(user_id,challenge):
    p=Path(settings.session_dir)/user_id
    p.mkdir(parents=True,exist_ok=True)
    return str(p/f'{challenge}.session')

def proxy_from_url(url):
    if not url:return None
    u=urlparse(url)
    return (u.scheme,u.hostname,u.port,True,u.username,u.password)

async def client_from_account(a):
    proxy=proxy_from_url(dec(a['proxy_url_enc'])) if a.get('proxy_url_enc') else None
    return TelegramClient(a['session_path'],int(a['api_id']),dec(a['api_hash_enc']),proxy=proxy)

async def bot_send_text_button(token,chat_id,text,button_text,button_url):
    async with httpx.AsyncClient(timeout=25) as h:
        r=await h.post(f'https://api.telegram.org/bot{token}/sendMessage',json={
            'chat_id':chat_id,'text':text,
            'reply_markup':{'inline_keyboard':[[{'text':button_text,'url':button_url}]]},
            'disable_web_page_preview':True
        })
        data=r.json()
        if not data.get('ok'):raise RuntimeError(data.get('description') or 'BOT_SEND_FAILED')
        return data['result']

async def prepare_source(client,bot_username,bot_token,self_uid,text,button_text,button_url):
    bot=await client.get_entity('@'+bot_username.lstrip('@'))
    before=await client.get_messages(bot,limit=1)
    baseline=before[0].id if before else 0
    await client.send_message(bot,'/start')
    await bot_send_text_button(bot_token,self_uid,text,button_text,button_url)
    for _ in range(30):
        for m in await client.get_messages(bot,limit=10):
            if not m.out and m.id>baseline and m.reply_markup is not None:return bot,m
        await asyncio.sleep(.3)
    raise RuntimeError('BOT_SOURCE_NOT_VISIBLE')

def is_rate_error(e):
    s=str(e).lower()
    return isinstance(e,(FloodWaitError,PeerFloodError)) or 'too many requests' in s or 'flood' in s
