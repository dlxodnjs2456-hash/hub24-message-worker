from .main import app
from . import db
from .settings import settings
from . import username_auth
from . import management
from . import marketplace
from . import market_admin_extra
from . import seller_extension
from . import seller_product_management
from . import admin_product_management
from . import market_vip
from . import market_pretrade_chat
from . import community
from . import telegram_communities
from . import telegram_community_categories
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
from . import inline_posts

VERSION='5.23.1'


def _drop_route(path, method=None):
    kept=[]
    for r in app.router.routes:
        same=getattr(r,'path',None)==path
        methods=getattr(r,'methods',set()) or set()
        if same and (method is None or method in methods):continue
        kept.append(r)
    app.router.routes=kept

for path,method in [('/health','GET'),('/v1/wallet/charge-requests','POST'),('/v1/wallet/charge-requests','GET'),('/v1/wallet/withdrawals','POST'),('/v1/admin/market/charges/{rid}/resolve','POST'),('/v1/market/products','POST'),('/v1/market/products/{pid}/buy','POST'),('/v1/market/trades/{tid}/evidence','GET'),('/v1/market/trades/{tid}/evidence','POST'),('/v1/admin/market/trades/{tid}/evidence','GET'),('/v1/wallet/usdt-charge-requests/{rid}/verify','POST'),('/v1/jobs','POST'),('/v1/checker/upload','POST'),('/v1/checker/jobs','GET'),('/v1/checker/jobs/{jid}/results','GET'),('/v1/checker/jobs/{jid}/download','GET'),('/v1/accounts/{aid}/dialogs/{peer_id}/messages','GET')]:_drop_route(path,method)

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
def health():return {'ok':True,'service':'hub24-worker','version':VERSION,'database':'supabase'}

@app.get('/health/db')
def health_db():
    result={'ok':True,'service':'hub24-worker','version':VERSION,'supabase_url_set':bool(settings.supabase_url),'service_role_set':bool(settings.supabase_service_role_key),'db_ok':False}
    try:db.sb.table('telegram_accounts').select('id').limit(1).execute();result['db_ok']=True;result['db_error']=None
    except Exception as e:result['db_error']=str(e)[:500]
    return result

@app.get('/health/management')
def health_management():
    return {'ok':True,'service':'hub24-worker','version':VERSION,'management_routes':True,'marketplace_routes':True,'community_routes':True,'telegram_community_directory_routes':True,'telegram_community_ranking':True,'telegram_community_likes':True,'telegram_community_comments':True,'telegram_community_category_admin':True,'username_signup':True,'legacy_email_users_preserved':True,'telegram_rate_limit_auto_failover':False,'telegram_operator_restriction_release':True,'telegram_restriction_release_auto_resume':False,'image_send_route':True,'image_send_job_mode':'IMAGE_INLINE','personal_inline_bot_routes':True,'checker_routes':True,'checker_cancel':True,'checker_restart_recovery':True,'legacy_manual_charge_routes':False,'legacy_withdrawal_route':False}
