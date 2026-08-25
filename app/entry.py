from .main import app
from . import db
from .settings import settings
from . import management
from . import marketplace
from . import market_admin_extra
from . import seller_extension
from . import market_vip
from . import community
from . import banner_slots
from . import referrals
from . import operations_hardening
from . import banner_admin_hardening
from . import usdt_autocharge
from . import member_admin
from . import admin_communications

VERSION='5.11.0'


def _drop_route(path, method=None):
    kept=[]
    for r in app.router.routes:
        same=getattr(r,'path',None)==path
        methods=getattr(r,'methods',set()) or set()
        if same and (method is None or method in methods):
            continue
        kept.append(r)
    app.router.routes=kept

# Remove legacy/bypass endpoints while preserving the current supported flows.
_drop_route('/health','GET')
_drop_route('/v1/wallet/charge-requests','POST')
_drop_route('/v1/wallet/charge-requests','GET')
_drop_route('/v1/wallet/withdrawals','POST')
_drop_route('/v1/admin/market/charges/{rid}/resolve','POST')
_drop_route('/v1/market/products','POST')
_drop_route('/v1/market/products/{pid}/buy','POST')
_drop_route('/v1/market/trades/{tid}/evidence','GET')
_drop_route('/v1/market/trades/{tid}/evidence','POST')
_drop_route('/v1/admin/market/trades/{tid}/evidence','GET')

from . import stability_hardening
from . import private_evidence

@app.get('/health')
def health():
    return {'ok':True,'service':'hub24-worker','version':VERSION,'database':'supabase'}

@app.get('/health/db')
def health_db():
    result={'ok':True,'service':'hub24-worker','version':VERSION,'supabase_url_set':bool(settings.supabase_url),'service_role_set':bool(settings.supabase_service_role_key),'db_ok':False}
    try:
        db.sb.table('telegram_accounts').select('id').limit(1).execute();result['db_ok']=True;result['db_error']=None
    except Exception as e:result['db_error']=str(e)[:500]
    return result

@app.get('/health/management')
def health_management():
    return {'ok':True,'service':'hub24-worker','version':VERSION,'management_routes':True,'marketplace_routes':True,'vip_market_routes':True,'community_routes':True,'banner_slot_routes':True,'referral_routes':True,'operations_hardening_routes':True,'banner_admin_hardening_routes':True,'usdt_autocharge_routes':True,'member_admin_routes':True,'admin_communications_routes':True,'stability_hardening_routes':True,'private_evidence_routes':True,'google_fx_auto_refresh':True,'usdt_verify_diagnostics':True,'legacy_manual_charge_routes':False,'legacy_withdrawal_route':False}
