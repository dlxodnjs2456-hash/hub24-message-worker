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
class ChargeCreate(BaseModel): amount:int

def parse_google_rate(text:str):
    rows=list(csv.reader(io.StringIO(text or '')))
    for row in rows:
        for cell in row:
            raw=str(cell or '').strip().replace(',','')
            m=re.search(r'([0-9]+(?:\.[0-9]+)?)',raw)
            if not m: continue
            try:
                value=float(m.group(1))
                if 500<=value<=3000:return value
            except Exception:pass
    raise ValueError('GOOGLE_RATE_NOT_FOUND')

async def refresh_google_rate(client=None):
    s=one('market_settings',id=1) or {};url=str(s.get('usdt_fx_csv_url') or '').strip()
    if str(s.get('usdt_fx_source') or '').upper()!='GOOGLE_SHEET' or not url:return float(s.get('usdt_krw_rate') or 0)
    own=client is None;c=client or httpx.AsyncClient()
    try:
        r=await c.get(url,timeout=15,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 NPay-FX/1.0'});r.raise_for_status();rate=parse_google_rate(r.text)
        q('market_settings').update({'usdt_krw_rate':rate,'usdt_fx_last_updated_at':now_iso(),'usdt_fx_last_error':None}).eq('id',1).execute();return rate
    except Exception as e:
        q('market_settings').update({'usdt_fx_last_error':str(e)[:300]}).eq('id',1).execute();return float(s.get('usdt_krw_rate') or 0)
    finally:
        if own:await c.aclose()

def user_charge_payload(user):
    s=one('market_settings',id=1) or {}
    reqs=q('npay_usdt_charge_requests').select('*').eq('user_id',user).order('created_at',desc=True).limit(30).execute().data or []
    active=next((x for x in reqs if x.get('status') in ('REQUESTED','VERIFY_REQUESTED')),None)
    return {'enabled':bool(s.get('usdt_autocharge_enabled',True)),'network':'TRON (TRC20)','token':'USDT','krw_rate':float(s.get('usdt_krw_rate') or 0),'rate_source':s.get('usdt_fx_source') or 'GOOGLE_SHEET','rate_updated_at':s.get('usdt_fx_last_updated_at'),'active_request':active,'requests':reqs}

@app.get('/v1/wallet/usdt-autocharge')
def usdt_autocharge_info(user=Depends(auth)):return user_charge_payload(user)

@app.post('/v1/wallet/usdt-charge-requests')
def create_usdt_charge(p:ChargeCreate,user=Depends(auth)):
    amount=int(p.amount or 0)
    if amount<=0:raise HTTPException(400,'INVALID_CHARGE_AMOUNT')
    s=one('market_settings',id=1) or {};rate=float(s.get('usdt_krw_rate') or 0)
    if not s.get('usdt_autocharge_enabled',True):raise HTTPException(409,'USDT_CHARGE_DISABLED')
    if rate<=0:raise HTTPException(503,'FX_RATE_UNAVAILABLE')
    old=q('npay_usdt_charge_requests').select('id').eq('user_id',user).in_('status',['REQUESTED','VERIFY_REQUESTED']).limit(1).execute().data or []
    if old:raise HTTPException(409,'ACTIVE_CHARGE_REQUEST_EXISTS')
    try:address=db.sb.rpc('npay_assign_usdt_address',{'p_user':user}).execute().data
    except Exception:address=None
    if not address:raise HTTPException(409,'NO_USDT_ADDRESS_AVAILABLE')
    ar=one('npay_usdt_deposit_addresses',address=address)
    if not ar:raise HTTPException(409,'ADDRESS_ASSIGNMENT_FAILED')
    required=round(amount/rate,6)
    row=q('npay_usdt_charge_requests').insert({'user_id':user,'address_id':ar['id'],'address':address,'requested_points':amount,'quoted_krw_rate':rate,'requested_usdt':required,'status':'REQUESTED'}).execute().data[0]
    return {'ok':True,'request':row}

@app.post('/v1/wallet/usdt-charge-requests/{rid}/verify')
def verify_usdt_charge(rid:int,user=Depends(auth)):
    r=one('npay_usdt_charge_requests',id=rid)
    if not r or str(r.get('user_id'))!=str(user):raise HTTPException(404,'CHARGE_REQUEST_NOT_FOUND')
    if r.get('status')=='PAID':return {'ok':True,'status':'PAID'}
    if r.get('status') not in ('REQUESTED','VERIFY_REQUESTED'):raise HTTPException(409,'INVALID_CHARGE_REQUEST_STATUS')
    q('npay_usdt_charge_requests').update({'status':'VERIFY_REQUESTED','verify_requested_at':now_iso(),'updated_at':now_iso()}).eq('id',rid).execute()
    return {'ok':True,'status':'VERIFY_REQUESTED'}

@app.get('/v1/admin/usdt-autocharge')
def admin_usdt_autocharge(user=Depends(auth)):
    if not is_admin(user):raise HTTPException(403,'ADMIN_REQUIRED')
    s=one('market_settings',id=1) or {};addresses=q('npay_usdt_deposit_addresses').select('*').order('id').limit(1000).execute().data or [];deposits=q('npay_usdt_deposits').select('*').order('detected_at',desc=True).limit(200).execute().data or [];requests=q('npay_usdt_charge_requests').select('*').order('created_at',desc=True).limit(200).execute().data or []
    return {'settings':{'usdt_krw_rate':float(s.get('usdt_krw_rate') or 0),'usdt_autocharge_enabled':bool(s.get('usdt_autocharge_enabled',True)),'usdt_fx_source':s.get('usdt_fx_source') or 'GOOGLE_SHEET','usdt_fx_last_updated_at':s.get('usdt_fx_last_updated_at'),'usdt_fx_last_error':s.get('usdt_fx_last_error')},'addresses':addresses,'deposits':deposits,'requests':requests}

@app.post('/v1/admin/usdt-autocharge/refresh-rate')
async def admin_refresh_usdt_rate(user=Depends(auth)):
    if not is_admin(user):raise HTTPException(403,'ADMIN_REQUIRED')
    rate=await refresh_google_rate()
    if rate<=0:raise HTTPException(502,'GOOGLE_RATE_REFRESH_FAILED')
    s=one('market_settings',id=1) or {};return {'ok':True,'usdt_krw_rate':rate,'updated_at':s.get('usdt_fx_last_updated_at'),'error':s.get('usdt_fx_last_error')}

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
    rows=q('market_settings').update({'usdt_autocharge_enabled':p.usdt_autocharge_enabled,'usdt_fx_source':'GOOGLE_SHEET'}).eq('id',1).execute().data or [];return rows[0] if rows else {'ok':True}

async def scan_request(client,req):
    address=req.get('address');uid=req.get('user_id');need=float(req.get('requested_usdt') or 0);rate=float(req.get('quoted_krw_rate') or 0)
    if not address or not uid or need<=0 or rate<=0:return False
    params={'only_confirmed':'true','only_to':'true','limit':50,'order_by':'block_timestamp,desc','contract_address':USDT_TRC20}
    try:params['min_timestamp']=int(datetime.fromisoformat(str(req.get('created_at')).replace('Z','+00:00')).timestamp()*1000)
    except Exception:pass
    headers={'TRON-PRO-API-KEY':settings.trongrid_api_key} if settings.trongrid_api_key else {}
    r=await client.get(f"{settings.trongrid_base_url.rstrip('/')}/v1/accounts/{address}/transactions/trc20",params=params,headers=headers,timeout=15)
    if r.status_code!=200:return False
    for tx in reversed((r.json() or {}).get('data') or []):
        if str(tx.get('to') or '')!=address:continue
        txid=str(tx.get('transaction_id') or '')
        if not txid or one('npay_usdt_deposits',txid=txid):continue
        info=tx.get('token_info') or {};decimals=int(info.get('decimals') or 6)
        try:amt=int(str(tx.get('value') or '0'))/(10**decimals)
        except Exception:continue
        if amt+0.000001<need:continue
        ts=tx.get('block_timestamp');block_iso=None
        if ts:
            try:block_iso=datetime.fromtimestamp(int(ts)/1000,timezone.utc).isoformat()
            except Exception:pass
        try:
            out=db.sb.rpc('npay_credit_usdt_deposit',{'p_txid':txid,'p_user':uid,'p_address':address,'p_from':tx.get('from'),'p_amount':amt,'p_rate':rate,'p_block_ts':block_iso}).execute().data
            credit=round(amt*rate)
            q('npay_usdt_charge_requests').update({'status':'PAID','matched_txid':txid,'actual_usdt':amt,'credit_points':credit,'paid_at':now_iso(),'updated_at':now_iso()}).eq('id',req['id']).execute()
            return bool(out or credit>=0)
        except Exception:return False
    return False

async def autocharge_loop():
    await asyncio.sleep(8);last_fx=0.0;loop=asyncio.get_running_loop()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                now=loop.time()
                if now-last_fx>=300:await refresh_google_rate(client);last_fx=now
                s=one('market_settings',id=1) or {}
                if s.get('usdt_autocharge_enabled',True):
                    reqs=q('npay_usdt_charge_requests').select('*').eq('status','VERIFY_REQUESTED').order('verify_requested_at').limit(200).execute().data or []
                    for req in reqs:
                        await scan_request(client,req);await asyncio.sleep(.15)
            except Exception:pass
            await asyncio.sleep(15)

@app.on_event('startup')
async def start_usdt_autocharge_loop():asyncio.create_task(autocharge_loop())
