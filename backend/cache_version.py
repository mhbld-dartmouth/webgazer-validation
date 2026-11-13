"""Cache-safe deployment identity for participant and researcher HTML.

Cloud Run K_REVISION changes at every deployment. Local/test builds use a
content digest so a change to any participant/admin source changes the identity.
"""
import hashlib
import html
import os
import re
from functools import lru_cache
from pathlib import Path
from fastapi.responses import HTMLResponse

_ROOT = Path(__file__).resolve().parent.parent
_NO_STORE = 'no-store, max-age=0, must-revalidate'

@lru_cache(maxsize=1)
def deployment_revision() -> str:
    revision = os.getenv('K_REVISION', '').strip()
    if revision and re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', revision):
        return revision
    digest = hashlib.sha256()
    for filename in ('index.html', 'backend/admin_dashboard.html', 'upload.js',
                     'quality.js', 'gaze_kinematics.js', 'backend/main.py',
                     'backend/admin_portal.py', 'backend/export_reports.py'):
        path = _ROOT / filename
        if path.is_file():
            digest.update(filename.encode('utf-8'))
            digest.update(path.read_bytes())
    return 'local-' + digest.hexdigest()[:20]


def no_store_headers() -> dict:
    return {'Cache-Control': _NO_STORE, 'Pragma': 'no-cache', 'Expires': '0'}


def serve_html(path: Path) -> HTMLResponse:
    revision = html.escape(deployment_revision(), quote=True)
    content = path.read_text(encoding='utf-8').replace('__DEPLOY_REVISION__', revision)
    return HTMLResponse(content=content, headers=no_store_headers())
