import asyncio
from datetime import datetime, timezone
import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth
from . import db
from .settings import settings

USDT_TRC20='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6T'


def q(table): return db.sb.table(table)

def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None

def is_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid); meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        return meta.get('role')=='admin'
    except Exception:return False

class AddressAdd(BaseModel):
    address:str
class UsdtSettings(BaseModel):
    usdt_krw_rate:float
    usdt_autocharge_enabled:bool=True

@app.get('/v1/wallet/usdt-autocharge')
def usdt_autocharge_info(user=Depends(auth)):
    s=one('market_settings',id=1) or {}
    address=None
    try: address=db.sb.rpc('npay_assign_usdt_address',{'p_user':user}).execute().data
    except Exception: pass
    deposits=q('npay_usdt_deposits').select('*').eq('user_id',user).order('detected_at',desc=True).limit(30).execute().data or []
    return {'enabled':bool(s.get('usdt_autocharge_enabled',True)),'address':address,'network':'TRON (TRC20)','token':'USDT','krw_rate':float(s.get('usdt_krw_rate') or 0),'deposits':deposits}

@app.get('/v1/admin/usdt-autocharge')
def admin_usdt_autocharge(user=Depends(auth)):
    if not is_admin(user): raise HTTPException(403,'ADMIN_REQUIRED')
    s=one('market_settings',id=1) or {}
    addresses=q('npay_usdt_deposit_addresses').select('*').order('id').limit(1000).execute().data or []
    deposits=q('npay_usdt_deposits').select('*').order('detected_at',desc=True).limit(200).execute().data or []
    return {'settings':{'usdt_krw_rate':float(s.get('usdt_krw_rate') or 0),'usdt_autocharge_enabled':bool(s.get('usdt_autocharge_enabled',True))},'addresses':addresses,'deposits':deposits}

@app.post('/v1/admin/usdt-autocharge/addresses')
def admin_add_usdt_address(p:AddressAdd,user=Depends(auth)):
    if not is_admin(user): raise HTTPException(403,'ADMIN_REQUIRED')
    a=p.address.strip()
    if len(a)<30 or not a.startswith('T'): raise HTTPException(400,'INVALID_TRON_ADDRESS')
    try:return q('npay_usdt_deposit_addresses').insert({'address':a}).execute().data[0]
    except Exception as e: raise HTTPException(409,'ADDRESS_ALREADY_EXISTS' if 'duplicate' in str(e).lower() else str(e))

@app.put('/v1/admin/usdt-autocharge/settings')
def admin_usdt_settings(p:UsdtSettings,user=Depends(auth)):
    if not is_admin(user): raise HTTPException(403,'ADMIN_REQUIRED')
    if p.usdt_krw_rate<=0: raise HTTPException(400,'INVALID_USDT_KRW_RATE')
    rows=q('market_settings').update({'usdt_krw_rate':p.usdt_krw_rate,'usdt_autocharge_enabled':p.usdt_autocharge_enabled}).eq('id',1).execute().data or []
    return rows[0] if rows else {'ok':True}

async def scan_address(client,address_row,rate):
    address=address_row.get('address'); uid=address_row.get('user_id')
    if not address or not uid:return 0
    params={'only_confirmed':'true','only_to':'true','limit':50,'order_by':'block_timestamp,desc','contract_address':USDT_TRC20}
    assigned=address_row.get('assigned_at')
    if assigned:
        try: params['min_timestamp']=int(datetime.fromisoformat(str(assigned).replace('Z','+00:00')).timestamp()*1000)
        except Exception: pass
    headers={'TRON-PRO-API-KEY':settings.trongrid_api_key} if settings.trongrid_api_key else {}
    r=await client.get(f"{settings.trongrid_base_url.rstrip('/')}/v1/accounts/{address}/transactions/trc20",params=params,headers=headers,timeout=15)
    if r.status_code!=200:return 0
    data=(r.json() or {}).get('data') or []
    credited=0
    for tx in reversed(data):
        if str(tx.get('to') or '')!=address: continue
        txid=str(tx.get('transaction_id') or '')
        if not txid: continue
        info=tx.get('token_info') or {}; decimals=int(info.get('decimals') or 6)
        try: amount=int(str(tx.get('value') or '0'))/(10**decimals)
        except Exception: continue
        if amount<=0: continue
        ts=tx.get('block_timestamp'); block_iso=None
        if ts:
            try:block_iso=datetime.fromtimestamp(int(ts)/1000,timezone.utc).isoformat()
            except Exception:pass
        try:
            out=db.sb.rpc('npay_credit_usdt_deposit',{'p_txid':txid,'p_user':uid,'p_address':address,'p_from':tx.get('from'),'p_amount':amount,'p_rate':rate,'p_block_ts':block_iso}).execute().data
            if out: credited+=1
        except Exception:
            pass
    return credited

async def autocharge_loop():
    await asyncio.sleep(8)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                s=one('market_settings',id=1) or {}
                if s.get('usdt_autocharge_enabled',True):
                    rate=float(s.get('usdt_krw_rate') or 0)
                    if rate>0:
                        rows=q('npay_usdt_deposit_addresses').select('*').eq('is_active',True).not_.is_('user_id','null').limit(500).execute().data or []
                        for row in rows:
                            await scan_address(client,row,rate)
                            await asyncio.sleep(.15)
            except Exception:
                pass
            await asyncio.sleep(30)

@app.on_event('startup')
async def start_usdt_autocharge_loop():
    asyncio.create_task(autocharge_loop())
