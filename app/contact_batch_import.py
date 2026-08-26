import asyncio, random
from telethon.tl import functions
from telethon.tl.types import InputPhoneContact

from . import main as base
from . import db
from .security import dec
from .telegram import client_from_account, prepare_source, is_rate_error

BATCH_SIZE=10

async def _import_waiting_batch(user,jid,aid,client):
    rows=db.rows('job_targets',user,eq={'job_id':jid,'state':'WAITING','assigned_account_id':int(aid)},order='created_at',limit=50)
    batch=[]
    for t in rows:
        if t.get('telegram_user_id'):
            continue
        batch.append(t)
        if len(batch)>=BATCH_SIZE:
            break
    if not batch:
        return False

    contacts=[];client_to_target={}
    for t in batch:
        cid=random.randrange(1,2**63)
        while cid in client_to_target:
            cid=random.randrange(1,2**63)
        client_to_target[cid]=t
        contacts.append(InputPhoneContact(client_id=cid,phone=t['phone'],first_name=f'H24-{str(t["id"])[:8]}',last_name=''))
        db.update('job_targets',{'stage':f'연락처 {len(batch)}개 묶음 추가','updated_at':base.now_iso()},eq={'id':t['id'],'user_id':user})

    base.event(user,jid,'INFO','CONTACT_BATCH',f'연락처 {len(batch)}개 묶음 추가 시작',aid)
    result=await client(functions.contacts.ImportContactsRequest(contacts))
    imported={int(x.client_id):int(x.user_id) for x in (getattr(result,'imported',None) or [])}

    resolved=0;unresolved=0
    for cid,t in client_to_target.items():
        uid=imported.get(int(cid))
        if uid:
            resolved+=1
            db.update('contacts',{'telegram_user_id':uid,'assigned_account_id':int(aid),'state':'RESOLVED','detail':'Telegram UID 확인'},eq={'id':t['contact_id'],'user_id':user})
            db.update('job_targets',{'telegram_user_id':uid,'stage':'연락처 확인 완료','updated_at':base.now_iso()},eq={'id':t['id'],'user_id':user})
        else:
            unresolved+=1
            db.update('job_targets',{'state':'FAILED','stage':'연락처 확인 실패','error_code':'CONTACT_NOT_RESOLVED','error_detail':'Telegram 사용자 확인 불가','updated_at':base.now_iso()},eq={'id':t['id'],'user_id':user})
            base.event(user,jid,'ERROR','TARGET',f"{t['phone']} 연락처 확인 실패 / CONTACT_NOT_RESOLVED",aid)

    base.event(user,jid,'INFO','CONTACT_BATCH',f'연락처 묶음 처리 완료 / 요청 {len(batch)} / 확인 {resolved} / 미확인 {unresolved}',aid)
    base.recalc_job(user,jid)
    return True

async def run_job_batched(user,jid):
    job=db.one('jobs',user,eq={'id':jid})
    accounts={str(a['id']):a for a in db.rows('telegram_accounts',user,order=None)}
    stop=base._stops[jid];pause=base._pauses[jid];clients={};sources={}
    db.update('jobs',{'status':'RUNNING','updated_at':base.now_iso()},eq={'id':jid,'user_id':user})
    base.event(user,jid,'INFO','JOB',f'작업 시작 / 연락처 추가 {BATCH_SIZE}개 묶음 처리')
    try:
        targets_all=db.rows('job_targets',user,eq={'job_id':jid},order='created_at')
        account_ids=sorted({str(x['assigned_account_id']) for x in targets_all if x.get('assigned_account_id')})
        for aid in account_ids:
            a=accounts.get(aid)
            if not a:continue
            c=await client_from_account(a);await c.connect();me=await c.get_me();clients[aid]=c
            bot,src=await prepare_source(c,job['bot_username'],dec(job['bot_token_enc']),me.id,job['message_text'],job['button_text'],job['button_url'])
            sources[aid]=(bot,src);base.event(user,jid,'INFO','SOURCE',f'텍스트+버튼 원본 준비 완료 message_id={src.id}',aid)

        while not stop.is_set():
            while pause.is_set() and not stop.is_set():
                await asyncio.sleep(.3)
            waiting=db.rows('job_targets',user,eq={'job_id':jid,'state':'WAITING'},order='created_at',limit=1)
            if not waiting:break
            first=waiting[0];aid=str(first['assigned_account_id']);c=clients.get(aid)
            if c is None:
                db.update('job_targets',{'state':'FAILED','stage':'실패','error_detail':'ASSIGNED_ACCOUNT_UNAVAILABLE','updated_at':base.now_iso()},eq={'id':first['id'],'user_id':user})
                base.recalc_job(user,jid);continue

            if not first.get('telegram_user_id'):
                try:
                    did=await _import_waiting_batch(user,jid,aid,c)
                    if did:continue
                except Exception as e:
                    if is_rate_error(e):
                        batch=db.rows('job_targets',user,eq={'job_id':jid,'state':'WAITING','assigned_account_id':int(aid)},order='created_at',limit=BATCH_SIZE)
                        for t in batch:
                            db.update('job_targets',{'state':'CHECK_REQUIRED','stage':'Telegram 제한','error_code':'TELEGRAM_RATE_LIMIT','error_detail':str(e),'updated_at':base.now_iso()},eq={'id':t['id'],'user_id':user})
                        db.update('jobs',{'status':'PAUSED','stop_reason':'TELEGRAM_RATE_LIMIT','updated_at':base.now_iso()},eq={'id':jid,'user_id':user})
                        base.event(user,jid,'CRITICAL','RATE_LIMIT',f'Telegram 제한 감지 / 전체 작업 중지 / {e}',aid);stop.set();break
                    db.update('job_targets',{'state':'FAILED','stage':'연락처 추가 실패','error_detail':str(e),'updated_at':base.now_iso()},eq={'id':first['id'],'user_id':user})
                    base.event(user,jid,'ERROR','TARGET',f"{first['phone']} 연락처 추가 실패 / {e}",aid);base.recalc_job(user,jid);continue

            waiting=db.rows('job_targets',user,eq={'job_id':jid,'state':'WAITING'},order='created_at',limit=1)
            if not waiting:continue
            t=waiting[0];tid=t['id'];aid=str(t['assigned_account_id']);c=clients.get(aid);bot,src=sources.get(aid,(None,None))
            uid=t.get('telegram_user_id')
            if not uid:continue
            try:
                if job.get('global_dedupe'):
                    history=db.rows('send_history',user,order='created_at',desc=True)
                    if any(h.get('phone')==t['phone'] or str(h.get('telegram_user_id'))==str(uid) for h in history):
                        db.update('job_targets',{'state':'SKIPPED','stage':'중복 제외','updated_at':base.now_iso()},eq={'id':tid,'user_id':user})
                        base.event(user,jid,'INFO','DEDUPE',f"{t['phone']} 기존 성공 이력으로 제외",aid);base.recalc_job(user,jid);continue
                db.update('job_targets',{'state':'PROCESSING','stage':'발송 중','updated_at':base.now_iso()},eq={'id':tid,'user_id':user})
                peer=await c.get_input_entity(uid);sent=await c.forward_messages(peer,src.id,from_peer=bot);msg=sent[0] if isinstance(sent,list) else sent;mid=int(msg.id)
                db.update('job_targets',{'state':'SENT','stage':'완료','message_id':mid,'updated_at':base.now_iso()},eq={'id':tid,'user_id':user})
                db.insert('send_history',{'user_id':user,'phone':t['phone'],'telegram_user_id':uid,'account_id':int(aid),'job_id':jid,'message_id':mid})
                a=accounts.get(aid) or {};db.update('telegram_accounts',{'sent_count':int(a.get('sent_count') or 0)+1},eq={'id':int(aid),'user_id':user});a['sent_count']=int(a.get('sent_count') or 0)+1
                base.event(user,jid,'INFO','SEND',f"{t['phone']} 발송 완료 message_id={mid}",aid);base.recalc_job(user,jid)
                await asyncio.sleep(random.uniform(float(job.get('delay_min') or 0),float(job.get('delay_max') or 0)))
            except Exception as e:
                if is_rate_error(e):
                    db.update('job_targets',{'state':'CHECK_REQUIRED','stage':'Telegram 제한','error_code':'TELEGRAM_RATE_LIMIT','error_detail':str(e),'updated_at':base.now_iso()},eq={'id':tid,'user_id':user})
                    db.update('jobs',{'status':'PAUSED','stop_reason':'TELEGRAM_RATE_LIMIT','updated_at':base.now_iso()},eq={'id':jid,'user_id':user})
                    base.event(user,jid,'CRITICAL','RATE_LIMIT',f'Telegram 제한 감지 / 전체 작업 중지 / {e}',aid);stop.set();break
                db.update('job_targets',{'state':'FAILED','stage':'실패','error_detail':str(e),'updated_at':base.now_iso()},eq={'id':tid,'user_id':user})
                base.event(user,jid,'ERROR','TARGET',f"{t['phone']} 실패 / {e}",aid);base.recalc_job(user,jid)

        current=db.one('jobs',user,eq={'id':jid})
        if current and current.get('status') not in ('PAUSED','STOPPED'):
            db.update('jobs',{'status':'COMPLETED','updated_at':base.now_iso()},eq={'id':jid,'user_id':user});base.event(user,jid,'INFO','JOB','작업 완료')
    finally:
        for c in clients.values():
            try:await c.disconnect()
            except Exception:pass
        base._tasks.pop(jid,None);base._stops.pop(jid,None);base._pauses.pop(jid,None)

# Existing /start route resolves base.run_job dynamically, so patch only the engine function.
base.run_job=run_job_batched
