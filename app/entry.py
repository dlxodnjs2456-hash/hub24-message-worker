from .main import app
from . import db
from .settings import settings
from . import management  # registers extended management routes
from . import marketplace  # registers wallet / marketplace / escrow routes
from . import market_admin_extra  # registers marketplace admin extras
from . import seller_extension  # registers seller profile extensions
from . import market_vip  # registers VIP seller / banners / image-required product routes
from . import community  # registers community boards / comments / cooldowns

@app.get('/health/db')
def health_db():
    result = {
        'ok': True,
        'service': 'hub24-worker',
        'version': '5.4.0',
        'supabase_url_set': bool(settings.supabase_url),
        'service_role_set': bool(settings.supabase_service_role_key),
        'db_ok': False,
    }
    try:
        db.sb.table('telegram_accounts').select('id').limit(1).execute()
        result['db_ok'] = True
        result['db_error'] = None
    except Exception as e:
        result['db_error'] = str(e)[:500]
    return result

@app.get('/health/management')
def health_management():
    return {
        'ok': True,
        'service': 'hub24-worker',
        'version': '5.4.0',
        'management_routes': True,
        'marketplace_routes': True,
        'vip_market_routes': True,
        'community_routes': True,
    }
