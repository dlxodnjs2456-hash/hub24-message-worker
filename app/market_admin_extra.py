from fastapi import Depends, HTTPException
from pydantic import BaseModel
from . import db
from .main import app, auth, now_iso
from .marketplace import require_admin, one, q

class ResolveWithdrawal(BaseModel):
    action:str

@app.post('/v1/admin/market/withdrawals/{wid}/resolve')
def resolve_withdrawal(wid:int,p:ResolveWithdrawal,user=Depends(auth)):
    require_admin(user)
    r=one('withdrawal_requests',id=wid)
    if not r or r.get('status')!='PENDING': raise HTTPException(409,'INVALID_WITHDRAWAL')
    action=p.action.upper()
    try:
        if action=='PAID':
            q('withdrawal_requests').update({'status':'PAID','updated_at':now_iso()}).eq('id',wid).execute()
            return {'ok':True,'status':'PAID'}
        if action=='REJECT':
            db.sb.rpc('hub24_reject_withdrawal',{'p_request_id':wid}).execute()
            return {'ok':True,'status':'REJECTED'}
        raise HTTPException(400,'INVALID_ACTION')
    except HTTPException: raise
    except Exception as e: raise HTTPException(400,str(e))
