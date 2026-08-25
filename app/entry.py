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

@app.get('/health/db')
def health_db():
    result={'ok':True,'service':'hub24-worker','version':'5.8.0','supabase_url_set':bool(settings.supabase_url),'service_role_set':bool(settings.supabase_service_role_key),'db_ok':False}
    try:
        db.sb.table('telegram_accounts').select('id').limit(1).execute();result['db_ok']=True;result['db_error']=None
    except Exception as e:result['db_error']=str(e)[:500]
    return result

@app.get('/health/management')
def health_management():
    return {'ok':True,'service':'hub24-worker','version':'5.8.0','management_routes':True,'marketplace_routes':True,'vip_market_routes':True,'community_routes':True,'banner_slot_routes':True,'referral_routes':True,'operations_hardening_routes':True,'banner_admin_hardening_routes':True,'usdt_autocharge_routes':True}
