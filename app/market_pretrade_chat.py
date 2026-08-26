from fastapi import Depends, HTTPException
from pydantic import BaseModel
from .main import app, auth, now_iso
from . import db


def q(name): return db.sb.table(name)

def one(name, **eq):
    x=q(name).select('*')
    for k,v in eq.items(): x=x.eq(k,v)
    rows=x.limit(1).execute().data or []
    return rows[0] if rows else None

class ChatOpen(BaseModel):
    seller_id:str
    product_id:int|None=None

class ChatMessage(BaseModel):
    message:str


def _thread_for_user(tid,user):
    t=one('npay_market_chat_threads',id=tid)
    if not t or user not in (str(t['buyer_id']),str(t['seller_id'])):
        raise HTTPException(403,'CHAT_ACCESS_DENIED')
    return t

@app.post('/v1/market/chats')
def open_chat(p:ChatOpen,user=Depends(auth)):
    if str(p.seller_id)==user: raise HTTPException(400,'SELF_CHAT_NOT_ALLOWED')
    seller=one('seller_profiles',user_id=p.seller_id)
    if not seller or seller.get('status')!='APPROVED': raise HTTPException(404,'SELLER_NOT_FOUND')
    if p.product_id is not None:
        product=one('market_products',id=p.product_id)
        if not product or str(product.get('seller_id'))!=str(p.seller_id): raise HTTPException(404,'PRODUCT_NOT_FOUND')
        rows=q('npay_market_chat_threads').select('*').eq('buyer_id',user).eq('seller_id',p.seller_id).eq('product_id',p.product_id).limit(1).execute().data or []
        if rows:return {'item':rows[0]}
    payload={'buyer_id':user,'seller_id':p.seller_id,'product_id':p.product_id,'status':'OPEN','updated_at':now_iso()}
    return {'item':q('npay_market_chat_threads').insert(payload).execute().data[0]}

@app.get('/v1/market/chats')
def list_chats(user=Depends(auth)):
    rows=q('npay_market_chat_threads').select('*').or_(f'buyer_id.eq.{user},seller_id.eq.{user}').order('updated_at',desc=True).limit(200).execute().data or []
    sellers={str(x['user_id']):x for x in q('seller_profiles').select('user_id,seller_name,telegram_username').execute().data or []}
    products={str(x['id']):x for x in q('market_products').select('id,title,image_url').execute().data or []}
    for t in rows:
        t['seller']=sellers.get(str(t['seller_id']))
        t['product']=products.get(str(t.get('product_id')))
    return {'items':rows}

@app.get('/v1/market/chats/{tid}/messages')
def chat_messages(tid:int,user=Depends(auth)):
    t=_thread_for_user(tid,user)
    rows=q('npay_market_chat_messages').select('*').eq('thread_id',tid).order('created_at').limit(1000).execute().data or []
    return {'thread':t,'items':rows}

@app.post('/v1/market/chats/{tid}/messages')
def send_chat_message(tid:int,p:ChatMessage,user=Depends(auth)):
    _thread_for_user(tid,user)
    msg=(p.message or '').strip()
    if not msg: raise HTTPException(400,'MESSAGE_REQUIRED')
    if len(msg)>4000: raise HTTPException(400,'MESSAGE_TOO_LONG')
    row=q('npay_market_chat_messages').insert({'thread_id':tid,'sender_id':user,'message':msg}).execute().data[0]
    q('npay_market_chat_threads').update({'updated_at':now_iso()}).eq('id',tid).execute()
    return row
