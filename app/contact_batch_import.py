import asyncio, random
from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException
from telethon.tl import functions
from telethon.tl.types import InputPhoneContact

from .main import app, auth, now_iso
from . import db
from .telegram import client_from_account, is_rate_error

BATCH_SIZE=10
_import_tasks={}

class ContactImportStart(BaseModel):
    account_ids:list[int]=Field(default_factory=list)
    max_contacts_per_account:int=Field(default=50, ge=1, le=60)


def _batch(user,bid):
    row=db.one('contact_batches',user,eq={'id':bid})
    if not row: raise HTTPException(404,'DB를 찾을 수 없습니다.')
    return row


def _ready_accounts(user,ids):
    rows=db.rows('telegram_accounts',user,eq={'status':'READY'},order='created_at')
    by_id={int(x['id']):x for x in rows};selected=[]
    for raw in ids:
        aid=int(raw)
        if aid in by_id and aid not in [int(x['id']) for x in selected]: selected.append(by_id[aid])
    if not selected: raise HTTPException(400,'READY Telegram 계정을 1개 이상 선택하세요.')
    return selected


def _allocate(contacts,accounts,per_account):
    capacity=len(accounts)*per_account
    if len(contacts)>capacity:
        raise HTTPException(400,f'계정 처리용량 부족: DB {len(contacts)}건 / 현재 최대 {capacity}건. 계정을 추가하거나 계정당 최대 처리개수를 늘려주세요.')
    allocations={int(a['id']):[] for a in accounts};idx=0
    for a in accounts:
        aid=int(a['id'])
        for _ in range(per_account):
            if idx>=len(contacts): break
            allocations[aid].append(contacts[idx]);idx+=1
    return allocations


def _progress_seed(accounts,allocations):
    out={}
    for a in accounts:
        aid=int(a['id']);rows=allocations.get(aid,[])
        out[str(aid)]={
            'account_id':aid,
            'label':a.get('label') or a.get('phone_masked') or f'계정 #{aid}',
            'status':'WAITING' if rows else 'EMPTY',
            'total':len(rows),'processed':0,'resolved':0,'failed':0,
        }
    return out


def _save_progress(user,bid,progress,processed,resolved,failed,**extra):
    payload={
        'contact_import_account_progress':progress,
        'contact_import_processed':processed,
        'contact_import_resolved':resolved,
        'contact_import_failed':failed,
    }
    payload.update(extra)
    db.update('contact_batches',payload,eq={'id':bid,'user_id':user})


async def _run_import(user,bid,account_ids,per_account):
    accounts=_ready_accounts(user,account_ids)
    contacts=db.rows('contacts',user,eq={'batch_id':bid},order='created_at')
    allocations=_allocate(contacts,accounts,per_account)
    clients={};processed=resolved=failed=0;progress=_progress_seed(accounts,allocations)
    db.update('contact_batches',{'contact_import_status':'RUNNING','contact_import_processed':0,'contact_import_resolved':0,'contact_import_failed':0,'contact_import_account_ids':[int(x) for x in account_ids],'contact_import_per_account':per_account,'contact_import_started_at':now_iso(),'contact_import_completed_at':None,'contact_import_error':None,'contact_import_account_progress':progress},eq={'id':bid,'user_id':user})
    try:
        for a in accounts:
            aid=int(a['id']);key=str(aid);rows=allocations.get(aid,[])
            if not rows:continue
            progress[key]['status']='RUNNING';_save_progress(user,bid,progress,processed,resolved,failed)
            c=await client_from_account(a);await c.connect();me=await c.get_me()
            if not me: raise RuntimeError(f'ACCOUNT_NOT_READY:{aid}')
            clients[aid]=c
            for start in range(0,len(rows),BATCH_SIZE):
                chunk=rows[start:start+BATCH_SIZE]
                request=[];mapping={}
                for item in chunk:
                    cid=random.randrange(1,2**63)
                    while cid in mapping: cid=random.randrange(1,2**63)
                    mapping[cid]=item
                    request.append(InputPhoneContact(client_id=cid,phone=item['phone'],first_name=f'N-{str(item["id"])[:8]}',last_name=''))
                try:
                    result=await c(functions.contacts.ImportContactsRequest(request))
                except Exception as e:
                    if is_rate_error(e):
                        progress[key]['status']='PAUSED';progress[key]['error']=str(e)[:300]
                        _save_progress(user,bid,progress,processed,resolved,failed,contact_import_status='PAUSED',contact_import_error=f'TELEGRAM_RATE_LIMIT: {str(e)[:500]}')
                        return
                    for item in chunk:
                        failed+=1;processed+=1;progress[key]['failed']+=1;progress[key]['processed']+=1
                        db.update('contacts',{'assigned_account_id':aid,'state':'IMPORT_FAILED','detail':str(e)[:500]},eq={'id':item['id'],'user_id':user})
                    _save_progress(user,bid,progress,processed,resolved,failed)
                    continue
                imported={int(x.client_id):int(x.user_id) for x in (getattr(result,'imported',None) or [])}
                for cid,item in mapping.items():
                    processed+=1;progress[key]['processed']+=1;uid=imported.get(int(cid))
                    if uid:
                        resolved+=1;progress[key]['resolved']+=1
                        db.update('contacts',{'telegram_user_id':uid,'assigned_account_id':aid,'state':'RESOLVED','detail':'연락처 추가 완료 / Telegram UID 확인'},eq={'id':item['id'],'user_id':user})
                    else:
                        failed+=1;progress[key]['failed']+=1
                        db.update('contacts',{'telegram_user_id':None,'assigned_account_id':aid,'state':'NOT_RESOLVED','detail':'연락처 추가 완료 / Telegram 사용자 확인 불가'},eq={'id':item['id'],'user_id':user})
                _save_progress(user,bid,progress,processed,resolved,failed)
                await asyncio.sleep(0)
            progress[key]['status']='COMPLETED';_save_progress(user,bid,progress,processed,resolved,failed)
        _save_progress(user,bid,progress,processed,resolved,failed,contact_import_status='COMPLETED',contact_import_completed_at=now_iso())
    except Exception as e:
        for v in progress.values():
            if v.get('status')=='RUNNING':v['status']='FAILED';v['error']=str(e)[:300]
        _save_progress(user,bid,progress,processed,resolved,failed,contact_import_status='FAILED',contact_import_error=str(e)[:1000])
    finally:
        for c in clients.values():
            try: await c.disconnect()
            except Exception: pass
        _import_tasks.pop(str(bid),None)


@app.post('/v1/batches/{bid}/import-contacts')
async def start_contact_import(bid:int,p:ContactImportStart,user=Depends(auth)):
    batch=_batch(user,bid);accounts=_ready_accounts(user,p.account_ids)
    contacts=db.rows('contacts',user,eq={'batch_id':bid},order='created_at')
    allocations=_allocate(contacts,accounts,p.max_contacts_per_account)
    key=str(bid)
    if key in _import_tasks and not _import_tasks[key].done(): raise HTTPException(409,'이미 연락처 추가 작업이 진행 중입니다.')
    progress=_progress_seed(accounts,allocations)
    db.update('contact_batches',{'contact_import_account_progress':progress},eq={'id':bid,'user_id':user})
    _import_tasks[key]=asyncio.create_task(_run_import(user,bid,[int(x['id']) for x in accounts],p.max_contacts_per_account))
    return {'ok':True,'batch_id':bid,'total_count':len(contacts),'account_count':len(accounts),'max_contacts_per_account':p.max_contacts_per_account,'batch_size':BATCH_SIZE,'status':'RUNNING','account_progress':progress}


@app.get('/v1/batches/{bid}/import-contacts')
def contact_import_status(bid:int,user=Depends(auth)):
    b=_batch(user,bid)
    return {'batch_id':bid,'status':b.get('contact_import_status') or 'NOT_STARTED','total_count':int(b.get('total_count') or 0),'processed':int(b.get('contact_import_processed') or 0),'resolved':int(b.get('contact_import_resolved') or 0),'failed':int(b.get('contact_import_failed') or 0),'account_ids':b.get('contact_import_account_ids') or [],'max_contacts_per_account':b.get('contact_import_per_account'),'account_progress':b.get('contact_import_account_progress') or {},'error':b.get('contact_import_error')}
