from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(t): return db.sb.table(t)
def one(t,**eq):
    x=q(t).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    r=x.limit(1).execute().data or []
    return r[0] if r else None

def require_admin(uid):
    try:
        r=db.sb.auth.admin.get_user_by_id(uid);meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        if meta.get('role')!='admin':raise HTTPException(403,'ADMIN_REQUIRED')
    except HTTPException:raise
    except Exception:raise HTTPException(403,'ADMIN_REQUIRED')

class TerminateBanner(BaseModel):
    reason:str
    refund_amount:int=0

@app.post('/v1/admin/market/banners/{bid}/terminate')
def terminate_paid_banner(bid:int,p:TerminateBanner,user=Depends(auth)):
    require_admin(user)
    b=one('market_banners',id=bid)
    if not b:raise HTTPException(404,'BANNER_NOT_FOUND')
    if not b.get('owner_user_id'):raise HTTPException(409,'NOT_PAID_BANNER')
    if not p.reason.strip():raise HTTPException(400,'TERMINATION_REASON_REQUIRED')
    refund=max(0,int(p.refund_amount or 0));owner=b['owner_user_id']
    if refund>0:
        db.sb.rpc('hub24_admin_credit_points',{'p_user':str(owner),'p_amount':refund,'p_memo':f'광고 슬롯 #{b.get("sort_order")} 관리자 종료 환불'}).execute()
    q('market_banners').update({'owner_user_id':None,'purchased_at':None,'expires_at':None,'is_lifetime':False,'plan_code':None,'purchase_price':None,'image_url':'','target_url':None,'is_active':False,'updated_at':now_iso()}).eq('id',bid).execute()
    q('admin_logs').insert({'admin_user_id':user,'action':'PAID_BANNER_TERMINATE','target_type':'banner','target_id':str(bid),'detail':{'slot':b.get('sort_order'),'owner_user_id':str(owner),'reason':p.reason.strip(),'refund_amount':refund}}).execute()
    q('user_notifications').insert({'user_id':owner,'notification_type':'BANNER_TERMINATED','title':'광고 이용 종료 안내','message':f'광고 슬롯 {b.get("sort_order")} 이용이 관리자에 의해 종료되었습니다. 환불 {refund:,}P. 사유: {p.reason.strip()}','link_url':'/market'}).execute()
    return {'ok':True,'refund_amount':refund}
