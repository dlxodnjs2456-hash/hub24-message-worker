from fastapi import Depends, HTTPException

from .main import app, auth
from .marketplace import q, one, require_admin


@app.delete('/v1/admin/market/categories/{cid}')
def admin_delete_category(cid: int, user=Depends(auth)):
    require_admin(user)
    category = one('market_categories', id=cid)
    if not category:
        raise HTTPException(404, 'CATEGORY_NOT_FOUND')

    product = q('market_products').select('id').eq('category_id', cid).limit(1).execute().data or []
    trade = q('escrow_trades').select('id').eq('category_id', cid).limit(1).execute().data or []
    if product or trade:
        raise HTTPException(
            409,
            '이 카테고리에 연결된 상품 또는 거래 기록이 있어 삭제할 수 없습니다. 기록 보존을 위해 숨김 처리해 주세요.'
        )

    try:
        rows = q('market_categories').delete().eq('id', cid).execute().data or []
    except Exception as e:
        raise HTTPException(409, f'카테고리를 삭제할 수 없습니다: {str(e)[:200]}')
    if not rows:
        raise HTTPException(404, 'CATEGORY_NOT_FOUND')
    return {'ok': True, 'deleted_id': cid}
