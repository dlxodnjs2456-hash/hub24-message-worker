from pydantic import BaseModel, Field
from fastapi import Depends
from .main import app, auth, now_iso
from . import db

class SendPrefs(BaseModel):
    message_text:str=''
    button_text:str=''
    button_url:str=''
    max_contacts_per_account:int=Field(default=50, ge=1, le=1000)

@app.get('/v1/telegram-send/preferences')
def get_send_preferences(user=Depends(auth)):
    row=db.sb.table('npay_telegram_send_preferences').select('*').eq('user_id',user).maybe_single().execute().data
    if not row:
        return {'message_text':'','button_text':'','button_url':'','max_contacts_per_account':50}
    return row

@app.put('/v1/telegram-send/preferences')
def put_send_preferences(p:SendPrefs,user=Depends(auth)):
    data={'user_id':user,'message_text':p.message_text,'button_text':p.button_text,'button_url':p.button_url,'max_contacts_per_account':p.max_contacts_per_account,'updated_at':now_iso()}
    db.sb.table('npay_telegram_send_preferences').upsert(data,on_conflict='user_id').execute()
    return data
