from .main import app
from . import db
from .settings import settings
from . import management
from . import marketplace
from . import market_admin_extra
from . import seller_extension
from . import seller_product_management
from . import admin_product_management
from . import market_vip
from . import market_pretrade_chat
from . import community
from . import banner_slots
from . import referrals
from . import admin_referral_codes
from . import admin_entitlements
from . import operations_hardening
from . import banner_admin_hardening
from . import usdt_autocharge
from . import member_admin
from . import admin_communications
from . import public_referral
from . import checker
from . import checker_cancel
from . import image_send

VERSION='5.20.0'


def _drop_route(path, method=None):
    kept=[]
    for r in app.router.routes:
        same=getattr(r,'path',None)==path
        methods=getattr(r,'methods',set()) or set()
        if same and (method is None or method in methods):
            continue
        kept.append(r)
    app.router.routes=kept

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
_drop_route('/v1/jobs','POST')
_drop_route('/v1/checker/upload','POST')
_drop_route('/v1/checker/jobs','GET')
_drop_route('/v1/checker/jobs/{jid}/results','GET')
_drop_route('/v1/checker/jobs/{jid}/download','GET')
_drop_route('/v1/accounts/{aid}/dialogs/{peer_id}/messages','GET')

app.router.on_startup=[fn for fn in app.router.on_startup if getattr(fn,'__name__','')!='start_usdt_autocharge_loop']

from . import stability_hardening
from . import private_evidence
from . import usdt_claim_hardening
from . import telegram_send_preferences
from . import job_assignment_hardening
from . import contact_batch_import
from . import telegram_phase64_hardening
from . import image_job_runner
from . import category_delete
from . import checker_parser_hardening
from . import checker_period_hardening
from . import checker_recovery
from . import chat_media_hardening

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
    return {'ok':True,'service':'hub24-worker','version':VERSION,'management_routes':True,'marketplace_routes':True,'marketplace_pretrade_chat':True,'seller_telegram_profile':True,'seller_product_management':True,'admin_product_management':True,'vip_market_routes':True,'community_routes':True,'banner_slot_routes':True,'referral_routes':True,'admin_referral_code_management':True,'admin_entitlement_grants':True,'operations_hardening_routes':True,'banner_admin_hardening_routes':True,'usdt_autocharge_routes':True,'member_admin_routes':True,'admin_communications_routes':True,'public_referral_validation':True,'stability_hardening_routes':True,'private_evidence_routes':True,'usdt_claim_hardening_routes':True,'job_account_assignment_routes':True,'contact_import_separate_phase':True,'contact_batch_import_size':10,'contact_import_account_progress':True,'telegram_send_saved_preferences':True,'telegram_inline_url_buttons':True,'telegram_account_fixed_partition':True,'telegram_cross_account_parallel':True,'telegram_same_account_single_worker':True,'telegram_rate_limit_auto_failover':False,'image_send_route':True,'image_send_job_mode':'IMAGE_POSTBOT','image_send_uses_normal_job_history':True,'telegram_chat_photo_preview':True,'telegram_chat_button_preview':True,'admin_category_delete':True,'checker_routes':True,'checker_cancel':True,'checker_period_hardening':True,'checker_activity_parser_hardening':True,'checker_period_matched_only':True,'checker_activity_semantics':True,'checker_drafts_hidden':True,'checker_restart_recovery':True,'checker_atomic_finalize':True,'checker_provider_mode':'TASK_POLL_EXPORT','checker_api_configured':bool(settings.check_api_base_url and settings.check_api_key),'google_fx_auto_refresh':True,'usdt_verify_diagnostics':True,'legacy_manual_charge_routes':False,'legacy_withdrawal_route':False}
