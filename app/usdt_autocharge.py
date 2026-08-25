import asyncio
import csv
import io
import re
from datetime import datetime, timezone
import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth
from . import db
from .settings import settings

USDT_TRC20='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'

def q(table): return db.sb.table(table)
def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None

def now_iso(): return datetime.now(timezone.utc).isoformat()

def is_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid);meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {};return meta.get('role')=='admin'
    except Exception:return False

class AddressAdd(BaseModel): address:str
class UsdtSettings(BaseModel): usdt_autocharge_enabled:bool=True

def parse_google_rate(text:str):
    rows=list(csv.reader(io.StringIO(text or '')))
    for row in rows:
        for cell in row:
            raw=str(cell or '').strip().replace(',','')
            m=re.search(r'([0-9]+(?:\.[0-9]+)?)',raw)
            if not m: continue
            try:
                value=float(m.group(1))
                if 500 <= value <= 3000:
                    return value
            except Exception:
                pass
    raise ValueError('GOOGLE_RATE_NOT_FOUND')

async def refresh_google_rate(client=None):
    s=one('market_settings',id=1) or {}
    if str(s.get('usdt_fx_source') or '').upper()!='GOOGLE_SHEET':
        return float(s.get('usdt_krw_rate') or 0)
    url=str(s.get('usdt_fx_csv_url') or '').strip()
    if not url:
        return float(s.get('usdt_krw_rate') or 0)
    own_client=client is None
    c=client or httpx.AsyncClient()
    try:
        r=await c.get(url,timeout=15,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 NPay-FX/1.0'})
        r.raise_for_status()
        rate=parse_google_rate(r.text)
        q('market_settings').update({'usdt_krw_rate':rate,'usdt_fx_last_updated_at':now_iso(),'usdt_fx_last_error':None}).eq('id',1).execute()
        return rate
    except Exception as e:
        q('market_settings').update({'usdt_fx_last_error':str(e)[:300]}).eq('id',1).execute()
        return float(s.get('usdt_krw_rate') or 0)
    finally:
        if own_client: await c.aclose()

@app.get('/v1/wallet/usdt-autocharge')
def usdt_autocharge_info(user=Depends(auth)):
    s=one('market_settings',id=1) or {};address=None
    try:address=db.sb.rpc('npay_assign_usdt_address',{'p_user':user}).execute().data
    except Exception:pass
    deposits=q('npay_usdt_deposits').select('*').eq('user_id',user).order('detected_at',desc=True).limit(30).execute().data or []
    return {'enabled':bool(s.get('usdt_autocharge_enabled',True)),'address':address,'network':'TRON (TRC20)','token':'USDT','krw_rate':float(s.get('usdt_krw_rate') or 0),'rate_source':s.get('usdt_fx_source') or 'MANUAL','rate_updated_at':s.get('usdt_fx_last_updated_at'),'deposits':deposits}

@app.get('/v1/admin/usdt-autocharge')
def admin_usdt_autocharge(user=Depends(auth)):
    if not is_admin(user):raise HTTPException(403,'ADMIN_REQUIRED')
    s=one('market_settings',id=1) or {};addresses=q('npay_usdt_deposit_addresses').select('*').order('id').limit(1000).execute().data or [];deposits=q('npay_usdt_deposits').select('*').order('detected_at',desc=True).limit(200).execute().data or []
    return {'settings':{'usdt_krw_rate':float(s.get('usdt_krw_rate') or 0),'usdt_autocharge_enabled':bool(s.get('usdt_autocharge_enabled',True)),'usdt_fx_source':s.get('usdt_fx_source') or 'MANUAL','usdt_fx_last_updated_at':s.get('usdt_fx_last_updated_at'),'usdt_fx_last_error':s.get('usdt_fx_last_error')},'addresses':addresses,'deposits':deposits}

@app.post('/v1/admin/usdt-autocharge/refresh-rate')
async def admin_refresh_usdt_rate(user=Depends(auth)):
    if not is_admin(user):raise HTTPException(403,'ADMIN_REQUIRED')
    rate=await refresh_google_rate()
    if rate<=0: raise HTTPException(502,'GOOGLE_RATE_REFRESH_FAILED')
    s=one('market_settings',id=1) or {}
    return {'ok':True,'usdt_krw_rate':rate,'updated_at':s.get('usdt_fx_last_updated_at'),'error':s.get('usdt_fx_last_error')}

@app.post('/v1/admin/usdt-autocharge/addresses')
def admin_add_usdt_address(p:AddressAdd,user=Depends(auth)):
    if not is_admin(user):raise HTTPException(403,'ADMIN_REQUIRED')
    a=p.address.strip()
    if len(a)<30 or not a.startswith('T'):raise HTTPException(400,'INVALID_TRON_ADDRESS')
    try:return q('npay_usdt_deposit_addresses').insert({'address':a}).execute().data[0]
    except Exception as e:raise HTTPException(409,'ADDRESS_ALREADY_EXISTS' if 'duplicate' in str(e).lower() else str(e))

@app.put('/v1/admin/usdt-autocharge/settings')
def admin_usdt_settings(p:UsdtSettings,user=Depends(auth)):
    if not is_admin(user):raise HTTPException(403,'ADMIN_REQUIRED')
    rows=q('market_settings').update({'usdt_autocharge_enabled':p.usdt_autocharge_enabled,'usdt_fx_source':'GOOGLE_SHEET'}).eq('id',1).execute().data or []
    return rows[0] if rows else {'ok':True}

async def scan_address(client,row,rate):
    address=row.get('address');uid=row.get('user_id')
    if not address or not uid:return 0
    params={'only_confirmed':'true','only_to':'true','limit':50,'order_by':'block_timestamp,desc','contract_address':USDT_TRC20}
    assigned=row.get('assigned_at')
    if assigned:
        try:params['min_timestamp']=int(datetime.fromisoformat(str(assigned).replace('Z','+00:00')).timestamp()*1000)
        except Exception:pass
    headers={'TRON-PRO-API-KEY':settings.trongrid_api_key} if settings.trongrid_api_key else {}
    r=await client.get(f"{settings.trongrid_base_url.rstrip('/')}/v1/accounts/{address}/transactions/trc20",params=params,headers=headers,timeout=15)
    if r.status_code!=200:return 0
    credited=0
    for tx in reversed((r.json() or {}).get('data') or []):
        if str(tx.get('to') or '')!=address:continue
        txid=str(tx.get('transaction_id') or '')
        if not txid:continue
        info=tx.get('token_info') or {};decimals=int(info.get('decimals') or 6)
        try:amount=int(str(tx.get('value') or '0'))/(10**decimals)
        except Exception:continue
        if amount<=0:continue
        block_iso=None;ts=tx.get('block_timestamp')
        if ts:
            try:block_iso=datetime.fromtimestamp(int(ts)/1000,timezone.utc).isoformat()
            except Exception:pass
        try:
            out=db.sb.rpc('npay_credit_usdt_deposit',{'p_txid':txid,'p_user':uid,'p_address':address,'p_from':tx.get('from'),'p_amount':amount,'p_rate':rate,'p_block_ts':block_iso}).execute().data
            if out:credited+=1
        except Exception:pass
    return credited

async def autocharge_loop():
    await asyncio.sleep(8)
    last_fx_refresh=0.0
    loop=asyncio.get_running_loop()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                now=loop.time()
                if now-last_fx_refresh>=300:
                    await refresh_google_rate(client)
                    last_fx_refresh=now
                s=one('market_settings',id=1) or {};rate=float(s.get('usdt_krw_rate') or 0)
                if s.get('usdt_autocharge_enabled',True) and rate>0:
                    rows=q('npay_usdt_deposit_addresses').select('*').eq('is_active',True).limit(500).execute().data or []
                    for row in rows:
                        if row.get('user_id'):
                            await scan_address(client,row,rate);await asyncio.sleep(.15)
            except Exception:pass
            await asyncio.sleep(30)

@app.on_event('startup')
async def start_usdt_autocharge_loop():asyncio.create_task(autocharge_loop())
