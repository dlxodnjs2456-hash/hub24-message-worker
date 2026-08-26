from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException

from .main import app, auth, event
from . import db
from .security import enc


class JobCreateAssigned(BaseModel):
    batch_id:int
    operation_mode:str='CONTACT_AND_SEND'
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
    if not batch:
        raise HTTPException(404,'batch not found')

    ready=db.rows('telegram_accounts',user,eq={'status':'READY'},order='created_at')
    ready_map={int(a['id']):a for a in ready}
    selected_ids=[]
    for raw in p.account_ids:
        aid=int(raw)
        if aid in ready_map and aid not in selected_ids:
            selected_ids.append(aid)
    if not selected_ids:
        raise HTTPException(400,'사용할 READY Telegram 계정을 1개 이상 선택하세요.')

    per_account=max(1,min(int(p.contacts_per_account or 50),60))
    contacts=db.rows('contacts',user,eq={'batch_id':p.batch_id},order='created_at')
    capacity=len(selected_ids)*per_account
    selected_contacts=contacts[:capacity]
    if not selected_contacts:
        raise HTTPException(400,'발송 대상 DB가 비어 있습니다.')

    job=db.insert('jobs',{
        'user_id':user,
        'batch_id':p.batch_id,
        'status':'WAITING',
        'operation_mode':p.operation_mode,
        'message_text':p.message_text,
        'button_text':p.button_text,
        'button_url':p.button_url,
        'bot_username':p.bot_username,
        'bot_token_enc':enc(p.bot_token),
        'delay_min':p.delay_min,
        'delay_max':p.delay_max,
        'global_dedupe':p.global_dedupe,
        'total_count':len(selected_contacts),
        'pending_count':len(selected_contacts),
        'selected_account_ids':selected_ids,
        'contacts_per_account':per_account,
        'source_batch_total':int(batch.get('total_count') or len(contacts)),
    })
    jid=job['id']

    targets=[]
    idx=0
    for aid in selected_ids:
        for _ in range(per_account):
            if idx>=len(selected_contacts):
                break
            c=selected_contacts[idx]
            idx+=1
            targets.append({
                'user_id':user,
                'job_id':jid,
                'contact_id':c['id'],
                'phone':c['phone'],
                'telegram_user_id':c.get('telegram_user_id'),
                'assigned_account_id':aid,
                'state':'WAITING',
                'stage':'대기',
            })
    db.insert_many('job_targets',targets)

    event(user,jid,'INFO','JOB',f'JOB 생성 / DB {int(batch.get("total_count") or len(contacts))}건 중 {len(targets)}건 배정 / 선택 계정 {len(selected_ids)}개 / 계정당 최대 {per_account}건')
    result=dict(job)
    result['assigned_count']=len(targets)
    result['source_batch_total']=int(batch.get('total_count') or len(contacts))
    result['selected_account_count']=len(selected_ids)
    result['contacts_per_account']=per_account
    return result
