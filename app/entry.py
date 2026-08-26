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
from . import public_referral
from . import checker

VERSION='5.14.0'


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
_drop_route('/v1/wallet/usdt-charge-requests/{rid}/verify','POST')
# Replace automatic all-READY account assignment with explicit per-job selection.
_drop_route('/v1/jobs','POST')
# Checker UI hardening replaces these routes without touching the Telegram send engine.
_drop_route('/v1/checker/upload','POST')
_drop_route('/v1/checker/jobs','GET')
_drop_route('/v1/checker/jobs/{jid}/results','GET')
_drop_route('/v1/checker/jobs/{jid}/download','GET')

# The original USDT loop is replaced with a DB-claim serialized loop.
app.router.on_startup=[fn for fn in app.router.on_startup if getattr(fn,'__name__','')!='start_usdt_autocharge_loop']

from . import stability_hardening
from . import private_evidence
from . import usdt_claim_hardening
from . import job_assignment_hardening
from . import checker_parser_hardening
from . import checker_period_hardening
from . import checker_recovery

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
    return {'ok':True,'service':'hub24-worker','version':VERSION,'management_routes':True,'marketplace_routes':True,'vip_market_routes':True,'community_routes':True,'banner_slot_routes':True,'referral_routes':True,'operations_hardening_routes':True,'banner_admin_hardening_routes':True,'usdt_autocharge_routes':True,'member_admin_routes':True,'admin_communications_routes':True,'public_referral_validation':True,'stability_hardening_routes':True,'private_evidence_routes':True,'usdt_claim_hardening_routes':True,'job_account_assignment_routes':True,'checker_routes':True,'checker_period_hardening':True,'checker_activity_parser_hardening':True,'checker_period_matched_only':True,'checker_activity_semantics':True,'checker_drafts_hidden':True,'checker_restart_recovery':True,'checker_atomic_finalize':True,'checker_provider_mode':'TASK_POLL_EXPORT','checker_api_configured':bool(settings.check_api_base_url and settings.check_api_key),'google_fx_auto_refresh':True,'usdt_verify_diagnostics':True,'legacy_manual_charge_routes':False,'legacy_withdrawal_route':False}
