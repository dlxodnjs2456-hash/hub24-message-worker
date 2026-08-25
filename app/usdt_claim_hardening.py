import asyncio
import uuid
import httpx
from fastapi import Depends, HTTPException

from .main import app, auth
from . import db
from .usdt_autocharge import one, q, now_iso, scan_request, refresh_google_rate


def claim(rid):
    token=uuid.uuid4()
    try:
        ok=db.sb.rpc('npay_claim_usdt_charge',{'p_request_id':rid,'p_token':str(token)}).execute().data
        return token if ok else None
    except Exception:
        return None

def release(rid,token):
    if not token:return
    try:db.sb.rpc('npay_release_usdt_charge_claim',{'p_request_id':rid,'p_token':str(token)}).execute()
    except Exception:pass

@app.post('/v1/wallet/usdt-charge-requests/{rid}/verify')
async def verify_usdt_charge_serialized(rid:int,user=Depends(auth)):
    r=one('npay_usdt_charge_requests',id=rid)
    if not r or str(r.get('user_id'))!=str(user):raise HTTPException(404,'CHARGE_REQUEST_NOT_FOUND')
    if r.get('status')=='PAID':return {'ok':True,'status':'PAID','paid':True}
    if r.get('status') not in ('REQUESTED','VERIFY_REQUESTED'):raise HTTPException(409,'INVALID_CHARGE_REQUEST_STATUS')
    if r.get('status')=='REQUESTED':
        q('npay_usdt_charge_requests').update({'status':'VERIFY_REQUESTED','verify_requested_at':now_iso(),'last_check_error':None,'updated_at':now_iso()}).eq('id',rid).execute()
    token=claim(rid)
    if not token:
        latest=one('npay_usdt_charge_requests',id=rid) or {}
        return {'ok':True,'status':latest.get('status'),'paid':latest.get('status')=='PAID','last_checked_at':latest.get('last_checked_at'),'last_check_error':'VERIFY_ALREADY_IN_PROGRESS'}
    try:
        fresh=one('npay_usdt_charge_requests',id=rid)
        async with httpx.AsyncClient() as client:
            paid=await scan_request(client,fresh)
        latest=one('npay_usdt_charge_requests',id=rid) or {}
        return {'ok':True,'status':latest.get('status'),'paid':paid,'last_checked_at':latest.get('last_checked_at'),'last_check_error':latest.get('last_check_error')}
    finally:
        release(rid,token)

async def serialized_autocharge_loop():
    await asyncio.sleep(8);last_fx=0.0;loop=asyncio.get_running_loop()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                now=loop.time()
                if now-last_fx>=300:
                    await refresh_google_rate(client);last_fx=now
                s=one('market_settings',id=1) or {}
                if s.get('usdt_autocharge_enabled',True):
                    reqs=q('npay_usdt_charge_requests').select('*').eq('status','VERIFY_REQUESTED').order('verify_requested_at').limit(200).execute().data or []
                    for req in reqs:
                        token=claim(req['id'])
                        if not token:continue
                        try:await scan_request(client,one('npay_usdt_charge_requests',id=req['id']) or req)
                        finally:release(req['id'],token)
                        await asyncio.sleep(.15)
            except Exception:
                pass
            await asyncio.sleep(15)

@app.on_event('startup')
async def start_serialized_usdt_autocharge_loop():
    asyncio.create_task(serialized_autocharge_loop())
