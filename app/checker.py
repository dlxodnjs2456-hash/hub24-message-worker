import asyncio, csv, io, math, re
from datetime import datetime, timezone
from pathlib import Path
import httpx
from fastapi import UploadFile, File, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook, Workbook

from .main import app, auth
from . import db
from .settings import settings

_checker_tasks={}

def now_iso(): return datetime.now(timezone.utc).isoformat()
def q(t): return db.sb.table(t)

def _display_phone(v:str):
    d=re.sub(r'\D','',v or '')
    if d.startswith('82') and len(d)>=12: d='0'+d[2:]
    if len(d)==11 and d.startswith('010'): return f'{d[:3]}-{d[3:7]}-{d[7:]}'
    return v

def _normalize(v):
    d=re.sub(r'\D','',str(v or ''))
    if d.startswith('82'): d='0'+d[2:]
    if len(d)==11 and d.startswith('010'): return '+82'+d[1:]
    return None

def _read_numbers(name,raw):
    ext=Path(name or '').suffix.lower(); out=[]
    if ext in ('.xlsx','.xlsm'):
        wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True);ws=wb.active
        headers=[]
        first=next(ws.iter_rows(values_only=True),None)
        if first:
            headers=[str(x or '').strip().lower() for x in first]
            aliases={'전화번호','휴대폰','휴대전화','연락처','phone','phone_number','mobile','mobile_number','tel'}
            idx=next((i for i,h in enumerate(headers) if h in aliases),None)
            if idx is not None:
                for rn,row in enumerate(ws.iter_rows(min_row=2,values_only=True),start=2):
                    if idx<len(row) and row[idx] is not None: out.append((rn,str(row[idx])))
            else:
                for rn,row in enumerate(ws.iter_rows(values_only=True),start=1):
                    for v in row:
                        if v is not None and len(re.sub(r'\D','',str(v)))>=10: out.append((rn,str(v)));break
    else:
        text=raw.decode('utf-8-sig',errors='ignore')
        rows=list(csv.reader(io.StringIO(text))) if ext=='.csv' else [[x] for x in text.splitlines()]
        for rn,row in enumerate(rows,start=1):
            for v in row:
                if len(re.sub(r'\D','',str(v)))>=10: out.append((rn,str(v)));break
    return out

def _rate(count):
    if count<1000:return 0
    if count<8000:return 1.0
    if count<50000:return .8
    if count<100000:return .7
    return .6

def _api_ready(): return bool(settings.check_api_base_url and settings.check_api_key)

def _api_url():
    base=settings.check_api_base_url.rstrip('/')
    ep=(settings.check_api_endpoint or '').strip()
    return base+('/'+ep.lstrip('/') if ep else '')

def _headers():
    typ=(settings.check_api_auth_type or 'BEARER').upper(); key=settings.check_api_key; sec=settings.check_api_secret
    if typ=='X_API_KEY': return {'X-API-Key':key}
    if typ=='BEARER': return {'Authorization':f'Bearer {key}'}
    if typ=='API_KEY_SECRET': return {'X-API-Key':key,'X-API-Secret':sec}
    return {}

def _interpret(data):
    # Adapter accepts common official-API response shapes. Unknown responses remain UNKNOWN, never guessed.
    raw=(data or {})
    status=str(raw.get('telegram_check_status') or raw.get('status') or '').upper()
    registered=raw.get('registered')
    if registered is True or status in ('REGISTERED','FOUND','ACTIVE'):
        out='REGISTERED'
    elif registered is False or status in ('NOT_REGISTERED','UNREGISTERED'):
        out='NOT_REGISTERED'
    elif status in ('RATE_LIMITED','TIMEOUT','API_ERROR'):
        out=status
    else: out='UNKNOWN'
    uid=raw.get('telegram_id') or raw.get('telegram_user_id') or raw.get('user_id')
    username=raw.get('telegram_username') or raw.get('username')
    active=raw.get('telegram_active') or raw.get('last_seen') or raw.get('active_at')
    return out,uid,username,active

async def _process(job_id,user):
    q('npay_checker_jobs').update({'status':'RUNNING','started_at':now_iso(),'updated_at':now_iso()}).eq('id',job_id).eq('user_id',user).execute()
    timeout=max(5,int(settings.check_api_timeout_seconds or 20)); limit=max(1,int(settings.check_api_rate_limit_per_minute or 20)); delay=60/limit
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                rows=q('npay_checker_items').select('*').eq('job_id',job_id).eq('user_id',user).eq('status','WAITING').order('id').limit(1).execute().data or []
                if not rows: break
                x=rows[0];payload={'phone':x['normalized_phone']}
                try:
                    typ=(settings.check_api_auth_type or 'BEARER').upper(); params=None
                    if typ=='QUERY': params={'api_key':settings.check_api_key,**payload}
                    r=await client.post(_api_url(),json=None if params else payload,params=params,headers=_headers())
                    if r.status_code==429: status='RATE_LIMITED'; data={}; err='HTTP 429'
                    elif r.status_code>=400: status='API_ERROR'; data={}; err=f'HTTP {r.status_code}'
                    else:
                        data=r.json();status,uid,username,active=_interpret(data);err=None
                    upd={'status':status,'api_status':str(r.status_code),'checked_at':now_iso(),'error_message':err}
                    if r.status_code<400:
                        _,uid,username,active=_interpret(data);upd.update({'telegram_id':uid,'telegram_username':username,'telegram_active':str(active) if active is not None else None})
                except httpx.TimeoutException:
                    upd={'status':'TIMEOUT','api_status':'TIMEOUT','checked_at':now_iso(),'error_message':'검수 API 응답 시간 초과'}
                except Exception as e:
                    upd={'status':'API_ERROR','api_status':'ERROR','checked_at':now_iso(),'error_message':str(e)[:500]}
                q('npay_checker_items').update(upd).eq('id',x['id']).eq('user_id',user).execute()
                await asyncio.sleep(delay)
        items=q('npay_checker_items').select('status').eq('job_id',job_id).eq('user_id',user).execute().data or []
        done=len(items);reg=sum(1 for x in items if x['status']=='REGISTERED');unk=sum(1 for x in items if x['status'] in ('UNKNOWN','NOT_REGISTERED'));err=sum(1 for x in items if x['status'] in ('API_ERROR','RATE_LIMITED','TIMEOUT'))
        q('npay_checker_jobs').update({'status':'COMPLETED','completed_count':done,'registered_count':reg,'unknown_count':unk,'error_count':err,'completed_at':now_iso(),'updated_at':now_iso()}).eq('id',job_id).eq('user_id',user).execute()
    except Exception as e:
        q('npay_checker_jobs').update({'status':'FAILED','error_message':str(e)[:1000],'updated_at':now_iso()}).eq('id',job_id).eq('user_id',user).execute()
    finally:_checker_tasks.pop(str(job_id),None)

@app.get('/v1/checker/config')
def checker_config(user=Depends(auth)):
    return {'minimum_count':1000,'maximum_count':1000000,'tiers':[{'min':1000,'max':7999,'unit_price':1.0},{'min':8000,'max':49999,'unit_price':.8},{'min':50000,'max':99999,'unit_price':.7},{'min':100000,'max':None,'unit_price':.6}],'api_ready':_api_ready()}

@app.post('/v1/checker/upload')
async def checker_upload(file:UploadFile=File(...),user=Depends(auth)):
    raw=await file.read()
    if len(raw)>25*1024*1024: raise HTTPException(413,'FILE_TOO_LARGE')
    vals=_read_numbers(file.filename or 'phones.xlsx',raw);seen=set();items=[];invalid=0;dup=0
    for rn,v in vals:
        n=_normalize(v)
        if not n: invalid+=1;continue
        if n in seen: dup+=1;continue
        seen.add(n);items.append((rn,v,n))
    count=len(items)
    if count<1000: raise HTTPException(400,f'MINIMUM_CHECK_COUNT_1000: valid={count}')
    if count>1000000: raise HTTPException(400,'MAXIMUM_CHECK_COUNT_1000000')
    rate=_rate(count);est=count*rate
    job=q('npay_checker_jobs').insert({'user_id':user,'original_filename':file.filename,'status':'DRAFT','uploaded_count':len(vals),'invalid_count':invalid,'duplicate_count':dup,'requested_count':count,'unit_price':rate,'estimated_cost':est}).execute().data[0]
    batch=[]
    for rn,v,n in items:
        batch.append({'job_id':job['id'],'user_id':user,'row_no':rn,'phone':_display_phone(v),'normalized_phone':n,'status':'WAITING'})
        if len(batch)>=1000:q('npay_checker_items').insert(batch).execute();batch=[]
    if batch:q('npay_checker_items').insert(batch).execute()
    return {'job':job,'quote':{'requested_count':count,'unit_price':rate,'estimated_cost':est,'charged_points':math.ceil(est),'invalid_count':invalid,'duplicate_count':dup}}

@app.get('/v1/checker/jobs')
def checker_jobs(user=Depends(auth)):
    return {'items':q('npay_checker_jobs').select('*').eq('user_id',user).order('created_at',desc=True).limit(100).execute().data or []}

@app.get('/v1/checker/jobs/{jid}')
def checker_job(jid:int,user=Depends(auth)):
    row=q('npay_checker_jobs').select('*').eq('id',jid).eq('user_id',user).maybe_single().execute().data
    if not row: raise HTTPException(404,'CHECKER_JOB_NOT_FOUND')
    return row

@app.post('/v1/checker/jobs/{jid}/start')
async def checker_start(jid:int,user=Depends(auth)):
    row=q('npay_checker_jobs').select('*').eq('id',jid).eq('user_id',user).maybe_single().execute().data
    if not row: raise HTTPException(404,'CHECKER_JOB_NOT_FOUND')
    if not _api_ready(): raise HTTPException(503,'CHECK_API_NOT_CONFIGURED')
    if row['status']=='DRAFT':
        try: charged=db.sb.rpc('npay_charge_checker_job',{'p_user':user,'p_job_id':jid}).execute().data
        except Exception as e: raise HTTPException(400,str(e))
    elif row['status'] not in ('QUEUED','FAILED'): raise HTTPException(409,'CHECKER_JOB_ALREADY_STARTED')
    key=str(jid)
    if key not in _checker_tasks or _checker_tasks[key].done(): _checker_tasks[key]=asyncio.create_task(_process(jid,user))
    return {'ok':True,'job_id':jid,'status':'RUNNING'}

@app.get('/v1/checker/jobs/{jid}/results')
def checker_results(jid:int,limit:int=200,user=Depends(auth)):
    return {'items':q('npay_checker_items').select('id,row_no,phone,status,telegram_id,telegram_username,telegram_active,checked_at,error_code,error_message').eq('job_id',jid).eq('user_id',user).order('id').limit(min(limit,1000)).execute().data or []}

@app.get('/v1/checker/jobs/{jid}/download')
def checker_download(jid:int,filter:str='all',user=Depends(auth)):
    job=q('npay_checker_jobs').select('*').eq('id',jid).eq('user_id',user).maybe_single().execute().data
    if not job: raise HTTPException(404,'CHECKER_JOB_NOT_FOUND')
    rows=q('npay_checker_items').select('*').eq('job_id',jid).eq('user_id',user).order('id').execute().data or []
    if filter=='registered': rows=[x for x in rows if x['status']=='REGISTERED']
    elif filter=='unknown': rows=[x for x in rows if x['status'] in ('UNKNOWN','NOT_REGISTERED')]
    elif filter=='error': rows=[x for x in rows if x['status'] in ('API_ERROR','RATE_LIMITED','TIMEOUT')]
    wb=Workbook();ws=wb.active;ws.title='검수결과';ws.append(['전화번호','가입여부','텔레그램 UID','텔레그램 ID','텔레그램 접속일자'])
    labels={'REGISTERED':'가입 확인','NOT_REGISTERED':'미가입','UNKNOWN':'확인 불가','API_ERROR':'오류','RATE_LIMITED':'호출 제한','TIMEOUT':'시간 초과','WAITING':'대기'}
    for x in rows: ws.append([x['phone'],labels.get(x['status'],x['status']),x.get('telegram_id'),x.get('telegram_username'),x.get('telegram_active')])
    bio=io.BytesIO();wb.save(bio);bio.seek(0);fn=f'npay_checker_{jid}_{filter}.xlsx'
    return StreamingResponse(bio,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{fn}"'})
