from fastapi import Depends, HTTPException, Header
from pydantic import BaseModel
from . import db
from .main import app, auth, _verify_supabase_jwt, now_iso


def q(table): return db.sb.table(table)

def one(table, **eq):
    x=q(table).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    d=x.limit(1).execute().data or []
    return d[0] if d else None

def admin_user(user):
    try:
        r=db.sb.auth.admin.get_user_by_id(user)
        meta=(getattr(r.user,'app_metadata',None) or {}) if r and r.user else {}
        return meta.get('role')=='admin'
    except Exception:
        return False

def require_admin(user):
    if not admin_user(user): raise HTTPException(403,'ADMIN_REQUIRED')

class SellerApply(BaseModel):
    seller_name:str; introduction:str|None=None
class ProductCreate(BaseModel):
    category_id:int|None=None; title:str; description:str|None=None; price:int; stock:int|None=None
class DirectTrade(BaseModel):
    seller_id:str; amount:int; title:str='직접 협의 거래'; description:str|None=None; category_id:int|None=None
class ProductTrade(BaseModel):
    quantity:int=1
class TradeMessage(BaseModel): message:str
class ChargeRequest(BaseModel): amount:int; note:str|None=None
class WithdrawalRequest(BaseModel): amount:int
class CategoryCreate(BaseModel): name:str; slug:str; sort_order:int=0
class CategoryUpdate(BaseModel): name:str|None=None; slug:str|None=None; sort_order:int|None=None; is_active:bool|None=None
class SettingsUpdate(BaseModel): withdrawal_fee_percent:float|None=None; minimum_withdrawal:int|None=None
class AdminSellerStatus(BaseModel): status:str
class AdminTradeResolve(BaseModel): action:str
class AdminChargeResolve(BaseModel): action:str

@app.get('/v1/market/categories')
def market_categories(user=Depends(auth)):
    return {'items':q('market_categories').select('*').eq('is_active',True).order('sort_order').execute().data or []}

@app.get('/v1/market/sellers')
def market_sellers(search:str='',user=Depends(auth)):
    x=q('seller_profiles').select('*').eq('status','APPROVED').order('completed_sales',desc=True).limit(100)
    if search: x=x.ilike('seller_name',f'%{search}%')
    return {'items':x.execute().data or []}

@app.post('/v1/market/seller/apply')
def seller_apply(p:SellerApply,user=Depends(auth)):
    old=one('seller_profiles',user_id=user)
    payload={'user_id':user,'seller_name':p.seller_name.strip(),'introduction':p.introduction,'status':'PENDING','updated_at':now_iso()}
    if old:
        return q('seller_profiles').update(payload).eq('user_id',user).execute().data[0]
    return q('seller_profiles').insert(payload).execute().data[0]

@app.get('/v1/market/seller/me')
def seller_me(user=Depends(auth)):
    return {'item':one('seller_profiles',user_id=user)}

@app.get('/v1/market/products')
def products(category_id:int|None=None,user=Depends(auth)):
    x=q('market_products').select('*').eq('status','ACTIVE').order('created_at',desc=True)
    if category_id is not None: x=x.eq('category_id',category_id)
    items=x.limit(300).execute().data or []
    sellers={str(s['user_id']):s for s in q('seller_profiles').select('*').eq('status','APPROVED').execute().data or []}
    cats={str(c['id']):c for c in q('market_categories').select('*').execute().data or []}
    for i in items:
        i['seller']=sellers.get(str(i['seller_id']))
        i['category']=cats.get(str(i.get('category_id')))
    return {'items':items}

@app.post('/v1/market/products')
def create_product(p:ProductCreate,user=Depends(auth)):
    s=one('seller_profiles',user_id=user)
    if not s or s.get('status')!='APPROVED': raise HTTPException(403,'APPROVED_SELLER_REQUIRED')
    if p.price<0: raise HTTPException(400,'INVALID_PRICE')
    return q('market_products').insert({'seller_id':user,'category_id':p.category_id,'title':p.title.strip(),'description':p.description,'price':p.price,'stock':p.stock,'status':'ACTIVE'}).execute().data[0]

@app.get('/v1/wallet')
def wallet(user=Depends(auth)):
    w=one('point_wallets',user_id=user)
    if not w:
        w=q('point_wallets').insert({'user_id':user}).execute().data[0]
    ledger=q('point_ledger').select('*').eq('user_id',user).order('created_at',desc=True).limit(100).execute().data or []
    return {'wallet':w,'ledger':ledger}

@app.post('/v1/wallet/charge-requests')
def charge_request(p:ChargeRequest,user=Depends(auth)):
    if p.amount<=0: raise HTTPException(400,'INVALID_AMOUNT')
    return q('point_charge_requests').insert({'user_id':user,'amount':p.amount,'note':p.note}).execute().data[0]

@app.get('/v1/wallet/charge-requests')
def charge_requests(user=Depends(auth)):
    return {'items':q('point_charge_requests').select('*').eq('user_id',user).order('created_at',desc=True).execute().data or []}

@app.post('/v1/wallet/withdrawals')
def withdrawal(p:WithdrawalRequest,user=Depends(auth)):
    try:
        r=db.sb.rpc('hub24_request_withdrawal',{'p_user':user,'p_amount':p.amount}).execute().data
        return {'ok':True,'id':r}
    except Exception as e: raise HTTPException(400,str(e))

@app.get('/v1/wallet/withdrawals')
def withdrawals(user=Depends(auth)):
    return {'items':q('withdrawal_requests').select('*').eq('user_id',user).order('created_at',desc=True).execute().data or []}

@app.post('/v1/market/products/{pid}/buy')
def buy_product(pid:int,p:ProductTrade,user=Depends(auth)):
    item=one('market_products',id=pid)
    if not item or item.get('status')!='ACTIVE': raise HTTPException(404,'PRODUCT_NOT_FOUND')
    qty=max(1,p.quantity); amount=int(item['price'])*qty
    if item.get('stock') is not None and int(item['stock'])<qty: raise HTTPException(409,'OUT_OF_STOCK')
    try:
        tid=db.sb.rpc('hub24_create_escrow_trade',{'p_buyer':user,'p_seller':item['seller_id'],'p_product_id':pid,'p_category_id':item.get('category_id'),'p_trade_type':'PRODUCT','p_title':item['title'],'p_description':item.get('description'),'p_amount':amount,'p_quantity':qty}).execute().data
        if item.get('stock') is not None: q('market_products').update({'stock':int(item['stock'])-qty}).eq('id',pid).execute()
        return {'ok':True,'trade_id':tid}
    except Exception as e: raise HTTPException(400,str(e))

@app.post('/v1/market/direct-escrow')
def direct_escrow(p:DirectTrade,user=Depends(auth)):
    s=one('seller_profiles',user_id=p.seller_id)
    if not s or s.get('status')!='APPROVED': raise HTTPException(404,'SELLER_NOT_FOUND')
    try:
        tid=db.sb.rpc('hub24_create_escrow_trade',{'p_buyer':user,'p_seller':p.seller_id,'p_product_id':None,'p_category_id':p.category_id,'p_trade_type':'DIRECT','p_title':p.title.strip(),'p_description':p.description,'p_amount':p.amount,'p_quantity':1}).execute().data
        return {'ok':True,'trade_id':tid}
    except Exception as e: raise HTTPException(400,str(e))

@app.get('/v1/market/trades')
def trades(user=Depends(auth)):
    a=q('escrow_trades').select('*').or_(f'buyer_id.eq.{user},seller_id.eq.{user}').order('created_at',desc=True).limit(300).execute().data or []
    sellers={str(s['user_id']):s for s in q('seller_profiles').select('*').execute().data or []}
    for t in a: t['seller']=sellers.get(str(t['seller_id']))
    return {'items':a}

@app.get('/v1/market/trades/{tid}/messages')
def trade_messages(tid:int,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])): raise HTTPException(403,'TRADE_ACCESS_DENIED')
    return {'items':q('trade_messages').select('*').eq('trade_id',tid).order('created_at').execute().data or []}

@app.post('/v1/market/trades/{tid}/messages')
def add_trade_message(tid:int,p:TradeMessage,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])): raise HTTPException(403,'TRADE_ACCESS_DENIED')
    return q('trade_messages').insert({'trade_id':tid,'sender_id':user,'sender_type':'USER','message':p.message}).execute().data[0]

@app.post('/v1/market/trades/{tid}/seller-complete')
def seller_complete(tid:int,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or str(t['seller_id'])!=user: raise HTTPException(403,'NOT_SELLER')
    if t['status'] not in ('ESCROWED','ACCEPTED'): raise HTTPException(409,'INVALID_TRADE_STATUS')
    q('escrow_trades').update({'seller_confirmed':True,'status':'SELLER_COMPLETED','updated_at':now_iso()}).eq('id',tid).execute()
    q('trade_messages').insert({'trade_id':tid,'sender_type':'SYSTEM','message':'판매자가 판매완료를 요청했습니다. 구매자가 확인하면 에스크로 포인트가 지급됩니다.'}).execute()
    return {'ok':True}

@app.post('/v1/market/trades/{tid}/buyer-complete')
def buyer_complete(tid:int,user=Depends(auth)):
    try:
        db.sb.rpc('hub24_release_escrow_trade',{'p_trade_id':tid,'p_buyer':user}).execute()
        return {'ok':True}
    except Exception as e: raise HTTPException(400,str(e))

@app.post('/v1/market/trades/{tid}/cancel-request')
def cancel_request(tid:int,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])): raise HTTPException(403,'TRADE_ACCESS_DENIED')
    other_confirmed=(t.get('cancel_requested_by') and str(t.get('cancel_requested_by'))!=user)
    if other_confirmed:
        try: db.sb.rpc('hub24_refund_escrow_trade',{'p_trade_id':tid}).execute(); return {'ok':True,'status':'CANCELLED'}
        except Exception as e: raise HTTPException(400,str(e))
    q('escrow_trades').update({'cancel_requested_by':user,'status':'CANCEL_REQUESTED','updated_at':now_iso()}).eq('id',tid).execute()
    q('trade_messages').insert({'trade_id':tid,'sender_type':'SYSTEM','message':'거래 취소 요청이 접수되었습니다. 상대방 동의 또는 관리자 중개가 필요합니다.'}).execute()
    return {'ok':True,'status':'CANCEL_REQUESTED'}

@app.post('/v1/market/trades/{tid}/dispute')
def dispute(tid:int,user=Depends(auth)):
    t=one('escrow_trades',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])): raise HTTPException(403,'TRADE_ACCESS_DENIED')
    q('escrow_trades').update({'status':'DISPUTED','updated_at':now_iso()}).eq('id',tid).execute()
    q('trade_messages').insert({'trade_id':tid,'sender_type':'SYSTEM','message':'분쟁이 접수되어 에스크로가 동결되었습니다. 관리자 확인 후 처리됩니다.'}).execute()
    return {'ok':True}

# admin
@app.get('/v1/admin/market/overview')
def admin_market_overview(user=Depends(auth)):
    require_admin(user)
    return {'settings':one('market_settings',id=1),'categories':q('market_categories').select('*').order('sort_order').execute().data or [],'sellers':q('seller_profiles').select('*').order('created_at',desc=True).limit(200).execute().data or [],'trades':q('escrow_trades').select('*').order('created_at',desc=True).limit(200).execute().data or [],'charges':q('point_charge_requests').select('*').order('created_at',desc=True).limit(200).execute().data or [],'withdrawals':q('withdrawal_requests').select('*').order('created_at',desc=True).limit(200).execute().data or []}

@app.post('/v1/admin/market/categories')
def admin_add_category(p:CategoryCreate,user=Depends(auth)):
    require_admin(user); return q('market_categories').insert(p.model_dump()).execute().data[0]
@app.put('/v1/admin/market/categories/{cid}')
def admin_update_category(cid:int,p:CategoryUpdate,user=Depends(auth)):
    require_admin(user); payload={k:v for k,v in p.model_dump().items() if v is not None}; return q('market_categories').update(payload).eq('id',cid).execute().data[0]
@app.put('/v1/admin/market/settings')
def admin_settings(p:SettingsUpdate,user=Depends(auth)):
    require_admin(user); payload={k:v for k,v in p.model_dump().items() if v is not None}; payload['updated_at']=now_iso(); return q('market_settings').update(payload).eq('id',1).execute().data[0]
@app.put('/v1/admin/market/sellers/{sid}')
def admin_seller_status(sid:str,p:AdminSellerStatus,user=Depends(auth)):
    require_admin(user); status=p.status.upper();
    if status not in ('PENDING','APPROVED','REJECTED','SUSPENDED'): raise HTTPException(400,'INVALID_STATUS')
    payload={'status':status,'updated_at':now_iso()};
    if status=='APPROVED': payload['approved_at']=now_iso()
    return q('seller_profiles').update(payload).eq('user_id',sid).execute().data[0]
@app.post('/v1/admin/market/trades/{tid}/resolve')
def admin_trade_resolve(tid:int,p:AdminTradeResolve,user=Depends(auth)):
    require_admin(user); t=one('escrow_trades',id=tid)
    if not t: raise HTTPException(404,'TRADE_NOT_FOUND')
    try:
        if p.action.upper()=='REFUND': db.sb.rpc('hub24_refund_escrow_trade',{'p_trade_id':tid}).execute()
        elif p.action.upper()=='RELEASE': db.sb.rpc('hub24_release_escrow_trade',{'p_trade_id':tid,'p_buyer':str(t['buyer_id'])}).execute()
        else: raise HTTPException(400,'INVALID_ACTION')
        return {'ok':True}
    except HTTPException: raise
    except Exception as e: raise HTTPException(400,str(e))
@app.post('/v1/admin/market/charges/{rid}/resolve')
def admin_charge_resolve(rid:int,p:AdminChargeResolve,user=Depends(auth)):
    require_admin(user); r=one('point_charge_requests',id=rid)
    if not r or r['status']!='PENDING': raise HTTPException(409,'INVALID_REQUEST')
    action=p.action.upper()
    if action=='APPROVE':
        db.sb.rpc('hub24_admin_credit_points',{'p_user':str(r['user_id']),'p_amount':int(r['amount']),'p_memo':f'충전 신청 #{rid} 승인'}).execute(); status='APPROVED'
    elif action=='REJECT': status='REJECTED'
    else: raise HTTPException(400,'INVALID_ACTION')
    q('point_charge_requests').update({'status':status,'updated_at':now_iso()}).eq('id',rid).execute(); return {'ok':True,'status':status}
