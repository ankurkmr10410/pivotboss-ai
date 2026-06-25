import sys, os

# Force UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Absolute paths
_root    = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.join(_root, 'backend')
_env     = os.path.join(_root, 'config', '.env')

# Load .env first
from dotenv import load_dotenv
load_dotenv(_env)

# THIS is the fix — add backend to sys.path here, permanently
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# Verify it works before going further
try:
    import api_routes  # noqa — just testing the path
except ModuleNotFoundError:
    print(f"ERROR: Cannot find api_routes in {_backend}")
    print(f"Files there: {os.listdir(_backend)}")
    sys.exit(1)

import argparse, uvicorn
from kotak_connector import get_connector, set_connector

parser = argparse.ArgumentParser()
parser.add_argument('--mock',  action='store_true')
parser.add_argument('--live',  action='store_true')
parser.add_argument('--login', action='store_true')
parser.add_argument('--port',  type=int, default=8000)
parser.add_argument('--host',  default='0.0.0.0')
args = parser.parse_args()

if args.mock:
    os.environ['PIVOTBOSS_MOCK'] = 'true'
elif args.live or args.login:
    os.environ['PIVOTBOSS_MOCK'] = 'false'

if args.login:
    conn = get_connector(mock=False, auto_login=False)
    if not conn.login_interactive():
        print('\nLogin failed. Fix credentials in config/.env and retry.')
        sys.exit(1)
    set_connector(conn)
    print('\nKotak login successful -- starting API server...')

from api_server import app
uvicorn.run(app, host=args.host, port=args.port, reload=False)
