import io, math
from datetime import datetime, timezone
from fastapi import UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from .main import app, auth
from . import db
from . import checker as base


def q(t): return db.sb.table(t)

def _active_days(v):
    if v is None or str(v).strip()=='': return None
    s=str(v).strip()
    try: return float(s)
    except Exception: pass
    try:
        dt=datetime.fromisoformat(s.replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return max(0,(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/86400)
    except Exception: return None

def _within(v,days):
    days=int(days or 0)
    if days==0:return True
    d=_active_days(v)
    return d is not None and d<=days

def _period_label(days):
    return '전체 활동 가입자' if int(days or 0)==0 else f'최근 {int(days)}일 활동 가입자'

def _registered_for_period(rows,days):
    registered=[x for x in rows if x.get('status')=='REGISTERED']
    matched=[x for x in registered if _within(x.get('telegram_active'),days)]
    # Some provider exports contain phone/UID/username only and omit the activity
    # column because the provider-side task already returned the filtered set.
    # Never return an empty detail/download solely because that optional column is absent.
    if not matched and registered and all(_active_days(x.get('telegram_active')) is None for x in registered):
        return registered, True
    return matched, False

@app.post('/v1/checker/upload')
async def checker_upload_v2(file:UploadFile=File(...),activity_days:int=Form(0),user=Depends(auth)):
    if activity_days not in (0,1,3,7): raise HTTPException(400,'INVALID_ACTIVITY_PERIOD')
    raw=await file.read()
    if len(raw)>25*1024*1024: raise HTTPException(413,'FILE_TOO_LARGE')
    vals=base._read_numbers(file.filename or 'phones.xlsx',raw);seen=set();items=[];invalid=0;dup=0
    for rn,v in vals:
        n=base._normalize(v)
        if not n: invalid+=1;continue
        if n in seen: dup+=1;continue
        seen.add(n);items.append((rn,v,n))
    count=len(items)
    if count>1000000: raise HTTPException(400,'MAXIMUM_CHECK_COUNT_1000000')
    eligible=count>=1000;rate=base._rate(count) if eligible else 0;est=count*rate
    quote={'uploaded_count':len(vals),'requested_count':count,'unit_price':rate,'estimated_cost':est,'charged_points':math.ceil(est),'invalid_count':invalid,'duplicate_count':dup,'eligible':eligible,'minimum_count':1000,'missing_count':max(0,1000-count),'activity_days':activity_days,'activity_period':_period_label(activity_days)}
    if not eligible:return {'job':None,'quote':quote}
    job=q('npay_checker_jobs').insert({'user_id':user,'original_filename':file.filename,'status':'DRAFT','uploaded_count':len(vals),'invalid_count':invalid,'duplicate_count':dup,'requested_count':count,'unit_price':rate,'estimated_cost':est,'activity_days':activity_days}).execute().data[0]
    batch=[]
    for rn,v,n in items:
        batch.append({'job_id':job['id'],'user_id':user,'row_no':rn,'phone':base._display_phone(v),'normalized_phone':n,'status':'WAITING'})
        if len(batch)>=1000:q('npay_checker_items').insert(batch).execute();batch=[]
    if batch:q('npay_checker_items').insert(batch).execute()
    return {'job':job,'quote':quote}

@app.get('/v1/checker/jobs')
def checker_jobs_v2(user=Depends(auth)):
    rows=q('npay_checker_jobs').select('*').eq('user_id',user).neq('status','DRAFT').order('created_at',desc=True).limit(100).execute().data or []
    return {'items':rows}

@app.get('/v1/checker/jobs/{jid}/results')
def checker_results_v2(jid:int,limit:int=200,user=Depends(auth)):
    job=q('npay_checker_jobs').select('activity_days').eq('id',jid).eq('user_id',user).maybe_single().execute().data
    if not job: raise HTTPException(404,'CHECKER_JOB_NOT_FOUND')
    days=int(job.get('activity_days') or 0)
    rows=q('npay_checker_items').select('id,row_no,phone,status,telegram_id,telegram_username,telegram_active,checked_at,error_code,error_message').eq('job_id',jid).eq('user_id',user).eq('status','REGISTERED').order('id').execute().data or []
    rows,fallback=_registered_for_period(rows,days)
    rows=rows[:min(limit,1000)]
    for x in rows:
        x['within_period']=True
        x['activity_period']=_period_label(days)
        x['provider_filtered_fallback']=fallback
    return {'items':rows,'activity_days':days,'activity_period':_period_label(days),'matched_count':len(rows),'provider_filtered_fallback':fallback}

@app.get('/v1/checker/jobs/{jid}/download')
def checker_download_v2(jid:int,filter:str='all',user=Depends(auth)):
    job=q('npay_checker_jobs').select('*').eq('id',jid).eq('user_id',user).maybe_single().execute().data
    if not job: raise HTTPException(404,'CHECKER_JOB_NOT_FOUND')
    days=int(job.get('activity_days') or 0)
    rows=q('npay_checker_items').select('*').eq('job_id',jid).eq('user_id',user).order('telegram_active',desc=True,nullsfirst=False).execute().data or []
    fallback=False
    if filter=='registered': rows,fallback=_registered_for_period(rows,days)
    elif filter=='unknown': rows=[x for x in rows if x['status'] in ('UNKNOWN','NOT_REGISTERED')]
    elif filter=='error': rows=[x for x in rows if x['status'] in ('API_ERROR','RATE_LIMITED','TIMEOUT')]
    wb=Workbook();ws=wb.active;ws.title='검수결과';ws.append(['전화번호','가입여부','텔레그램 UID','텔레그램 ID','텔레그램 접속일자','활동 기간'])
    labels={'REGISTERED':'가입 확인','NOT_REGISTERED':'미가입','UNKNOWN':'확인 불가','API_ERROR':'오류','RATE_LIMITED':'호출 제한','TIMEOUT':'시간 초과','WAITING':'대기'}
    for x in rows:
        matched=x.get('status')=='REGISTERED' and (_within(x.get('telegram_active'),days) or fallback)
        activity=_period_label(days) if matched else '-'
        active=x.get('telegram_active')
        if matched and fallback and active in (None,'') and days==1:
            active='0'
        ws.append([x['phone'],labels.get(x['status'],x['status']),x.get('telegram_id'),x.get('telegram_username'),active,activity])
    bio=io.BytesIO();wb.save(bio);bio.seek(0);fn=f'npay_checker_{jid}_{filter}.xlsx'
    return StreamingResponse(bio,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{fn}"'})
