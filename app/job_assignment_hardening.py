from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException

from .main import app, auth, event
from . import db
from .security import enc

class JobCreateAssigned(BaseModel):
    batch_id:int
    operation_mode:str='SEND_RESOLVED_CONTACTS'
    message_text:str
    button_text:str
    button_url:str
    bot_username:str
    bot_token:str
    delay_min:float=2
    delay_max:float=5
    global_dedupe:bool=True
    account_ids:list[int]=Field(default_factory=list)
    contacts_per_account:int=Field(default=50, ge=1, le=60)

@app.post('/v1/jobs')
def create_job_assigned(p:JobCreateAssigned,user=Depends(auth)):
    batch=db.one('contact_batches',user,eq={'id':p.batch_id})
    if not batch: raise HTTPException(404,'batch not found')
    if (batch.get('contact_import_status') or '')!='COMPLETED':
        raise HTTPException(409,'먼저 연락처 추가 작업을 완료하세요.')

    ready=db.rows('telegram_accounts',user,eq={'status':'READY'},order='created_at')
    ready_map={int(a['id']):a for a in ready}
    requested=[int(x) for x in p.account_ids] if p.account_ids else [int(x) for x in (batch.get('contact_import_account_ids') or [])]
    selected_ids=[]
    for aid in requested:
        if aid in ready_map and aid not in selected_ids: selected_ids.append(aid)
    if not selected_ids: raise HTTPException(400,'사용할 READY Telegram 계정을 1개 이상 선택하세요.')

    contacts=db.rows('contacts',user,eq={'batch_id':p.batch_id,'state':'RESOLVED'},order='created_at')
    contacts=[c for c in contacts if c.get('telegram_user_id') and int(c.get('assigned_account_id') or 0) in selected_ids]
    if not contacts: raise HTTPException(400,'발송 가능한 연락처가 없습니다. 먼저 연락처 추가 결과를 확인하세요.')

    job=db.insert('jobs',{'user_id':user,'batch_id':p.batch_id,'status':'WAITING','operation_mode':'SEND_RESOLVED_CONTACTS','message_text':p.message_text,'button_text':p.button_text,'button_url':p.button_url,'bot_username':p.bot_username,'bot_token_enc':enc(p.bot_token),'delay_min':p.delay_min,'delay_max':p.delay_max,'global_dedupe':p.global_dedupe,'total_count':len(contacts),'pending_count':len(contacts),'selected_account_ids':selected_ids,'contacts_per_account':int(batch.get('contact_import_per_account') or p.contacts_per_account or 50),'source_batch_total':int(batch.get('total_count') or len(contacts))})
    jid=job['id'];targets=[]
    for c in contacts:
        targets.append({'user_id':user,'job_id':jid,'contact_id':c['id'],'phone':c['phone'],'telegram_user_id':c.get('telegram_user_id'),'assigned_account_id':int(c['assigned_account_id']),'state':'WAITING','stage':'발송 대기'})
    db.insert_many('job_targets',targets)
    event(user,jid,'INFO','JOB',f'발송 JOB 생성 / 연락처 추가 완료 대상 {len(targets)}건 / 선택 계정 {len(selected_ids)}개')
    result=dict(job);result['assigned_count']=len(targets);result['selected_account_count']=len(selected_ids);return result
