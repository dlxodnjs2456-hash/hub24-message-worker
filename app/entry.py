from .main import app
from . import db
from .settings import settings
from . import management  # registers extended management routes
from . import marketplace  # registers wallet / marketplace / escrow routes
from . import market_admin_extra  # registers marketplace admin extras

@app.get('/health/db')
def health_db():
    result = {
        'ok': True,
        'service': 'hub24-worker',
        'version': '5.2.1',
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
        'version': '5.2.1',
        'management_routes': True,
        'marketplace_routes': True,
    }
