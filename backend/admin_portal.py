"""Password-only researcher portal. Bearer token is held in PAGE MEMORY ONLY.
Closing or refreshing admin webpage requires password again; no auth cookie or storage.
An issued token cannot be recalled on tab-close and expires in 30 minutes; never log tokens.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from export_reports import objects, read_json, build_export
from retention import cleanup_stale

HERE = Path(__file__).resolve().parent
SID_RE = re.compile(r'^[a-f0-9]{32}$')
LOGIN_MAX_ATTEMPTS = 7
LOGIN_WINDOW_SECONDS = 900
_failed = defaultdict(deque)
_password = PasswordHasher()


def _conf(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError('Missing researcher security setting: ' + name)
    return value


def check_admin_config():
    p = _conf('ADMIN_PASSWORD_HASH')
    if not p.startswith('$argon2id$'):
        raise RuntimeError('ADMIN_PASSWORD_HASH must be an Argon2id hash, not a plaintext password')
    for key in ('ADMIN_SESSION_SECRET', 'STUDY_SECRET'):
        if len(_conf(key)) < 40:
            raise RuntimeError(key + ' must be at least 40 random characters')


def _signed(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()).rstrip(b'=')
    digest = hmac.new(_conf('ADMIN_SESSION_SECRET').encode(), encoded, hashlib.sha256).digest()
    return encoded.decode() + '.' + base64.urlsafe_b64encode(digest).rstrip(b'=').decode()



# Every private dashboard or download request needs a valid researcher token.
def _require(request):
    value = request.headers.get('authorization', '')
    if not value.startswith('Bearer ') or len(value) > 2000:
        raise HTTPException(401, 'Researcher sign in required')
    try:
        body, signature = value[7:].split('.', 1)
        mac = base64.urlsafe_b64decode(signature + '=' * (-len(signature)%4))
        expected = hmac.new(_conf('ADMIN_SESSION_SECRET').encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise ValueError()
        obj = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body)%4)))
        if obj.get('role') != 'researcher' or obj.get('version') != 2 or int(obj.get('exp',0)) <= time.time():
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError) as e:
        raise HTTPException(401, 'Session expired or invalid; sign in again') from e


def _ip(request):
    return request.client.host if request.client else 'unknown'


def _recent_failures(ip):
    now = time.monotonic()
    q = _failed[ip]
    while q and q[0] < now - LOGIN_WINDOW_SECONDS:
        q.popleft()
    return q


def _stream_file(file, filename):
    def reader():
        try:
            while True:
                block = file.read(256*1024)
                if not block: break
                yield block
        finally:
            file.close()
    return StreamingResponse(reader(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def register(app, get_s3, get_bucket):
    @app.get('/admin', include_in_schema=False)
    @app.get('/admin/', include_in_schema=False)
    @app.get('/admin/login', include_in_schema=False)
    def dashboard_shell():
        # HTML shell is public; it contains NO private data and cannot call APIs until login.
        return FileResponse(HERE/'admin_dashboard.html', media_type='text/html', headers={'Cache-Control':'no-store'})

    # Check the password hash. The login response is held in page memory, not a cookie.
    @app.post('/admin/api/login', include_in_schema=False)
    async def login(request: Request):
        failures = _recent_failures(_ip(request))
        if len(failures) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, 'Too many attempts. Try again later.')
        raw = await request.body()
        if len(raw) > 2048:
            raise HTTPException(413, 'Request too large')
        try:
            data = json.loads(raw)
            password = data.get('password', '')
        except (ValueError, TypeError, AttributeError):
            password = ''
        if not isinstance(password, str) or len(password) > 256:
            password = ''
        try:
            good = _password.verify(_conf('ADMIN_PASSWORD_HASH'), password)
        except (InvalidHashError, VerifyMismatchError):
            good = False
        if not good:
            failures.append(time.monotonic())
            raise HTTPException(401, 'Incorrect password')
        failures.clear()
        token = _signed({'role':'researcher', 'version':2,
                         'exp':int(time.time())+30*60, 'nonce':secrets.token_hex(16)})
        return JSONResponse({'token':token, 'expires_in_seconds':1800}, headers={'Cache-Control':'no-store'})

    # Only confirmed submissions appear in the participant table. Unreadable
    # completion receipts are surfaced separately rather than silently ignored.
    @app.get('/admin/api/sessions', include_in_schema=False)
    def sessions(request: Request):
        _require(request)
        s3, bucket = get_s3(), get_bucket()
        grouped = defaultdict(list)
        for item in objects(s3, bucket, 'sessions/'):
            parts = item['Key'].split('/')
            if len(parts) >= 3 and SID_RE.fullmatch(parts[1]):
                grouped[parts[1]].append(item)
        records = []
        warnings = []
        for sid, items in grouped.items():
            if not any(v['Key'] == f'sessions/{sid}/completed.json.gz' for v in items):
                continue  # Never show unfinished uploads as completed records.
            try:
                complete = read_json(s3, bucket, f'sessions/{sid}/completed.json.gz')
            except Exception:
                # Protect and flag this record. It may contain recoverable study data.
                warnings.append({'session_id':sid, 'issue':'Unreadable completion receipt'})
                continue
            if complete.get('partial'):
                continue
            item = {'session_id':sid, 'session_number':complete.get('session_number'),
                    'submitted_at_utc':complete.get('submitted_at_utc'), 'status':'submitted',
                    'batches':sum('/batches/' in x['Key'] for x in items),
                    'bytes':sum(int(x.get('Size',0)) for x in items),
                    'last_activity':None, 'visit':'pilot'}
            for obj in items:
                stamp = obj.get('LastModified')
                if isinstance(stamp, datetime):
                    iso = stamp.astimezone(timezone.utc).isoformat()
                    if not item['last_activity'] or iso > item['last_activity']:
                        item['last_activity'] = iso
            records.append(item)
        records.sort(key=lambda x:x['last_activity'] or '', reverse=True)
        for item in records[:250]:
            sid = item['session_id']
            try:
                complete = read_json(s3, bucket, f'sessions/{sid}/completed.json.gz')
                seq = int(complete['last_sequence'])
                batch = read_json(s3, bucket, f'sessions/{sid}/batches/{seq:06d}.json.gz')
                summary = (batch.get('data') or {}).get('validation_summary') or {}
                item['metrics'] = {x:summary.get(x) for x in ('accPx','accDeg','precPx','precDeg','hz','im','isd','validity')}
                timing = summary.get('sampling_timing') or {}
                item['metrics']['sampling_interval_count'] = timing.get('interval_count')
                item['metrics']['camera_reported_frame_rate_fps'] = timing.get('camera_reported_frame_rate_fps')
                item['metrics']['camera_observed_frame_rate_fps'] = timing.get('observed_camera_fps')
                item['metrics']['camera_observed_interval_mean_ms'] = timing.get('observed_frame_interval_mean_ms')
                item['validation_passed'] = (summary.get('screenQuality') or {}).get('pass')
            except Exception:
                item['metrics_error'] = True
        return {'total_sessions':len(records),
                'counts':{'submitted':len(records), 'needs_review':len(warnings)},
                'sessions':records[:250], 'shown':min(250,len(records)),
                'needs_review':warnings[:20],
                'refreshed_at':datetime.now(timezone.utc).isoformat()}

    # Explicit researcher-controlled cleanup; never deletes a session that has a
    # completion receipt, even if its content cannot be read.
    @app.post('/admin/api/cleanup-stale', include_in_schema=False)
    def cleanup(request: Request, dry_run: bool = True):
        _require(request)
        return cleanup_stale(get_s3(), get_bucket(), dry_run=dry_run)

    # A separate, deliberately opened storage management list includes incomplete
    # sessions, including receipts requiring investigation. It does not expose
    # raw participant data or any study rows to unauthenticated users.
    @app.get('/admin/api/stored-sessions', include_in_schema=False)
    def stored_sessions(request: Request):
        _require(request)
        grouped = defaultdict(list)
        for item in objects(get_s3(), get_bucket(), 'sessions/'):
            parts = item['Key'].split('/')
            if len(parts) >= 3 and SID_RE.fullmatch(parts[1]):
                grouped[parts[1]].append(item)
        records = []
        for sid, items in grouped.items():
            has_receipt = any(x['Key'] == f'sessions/{sid}/completed.json.gz' for x in items)
            state = 'receipt_needs_review' if has_receipt else 'unfinished'
            number = None
            if has_receipt:
                try:
                    receipt = read_json(get_s3(), get_bucket(), f'sessions/{sid}/completed.json.gz')
                    state = 'submitted' if not receipt.get('partial') else 'partial_receipt'
                    number = receipt.get('session_number')
                except Exception:
                    pass
            last = max((x.get('LastModified') for x in items
                        if isinstance(x.get('LastModified'), datetime)), default=None)
            records.append({'session_id':sid, 'session_number':number,
                            'status':state, 'objects':len(items),
                            'bytes':sum(int(x.get('Size',0)) for x in items),
                            'last_activity_utc':last.astimezone(timezone.utc).isoformat() if last else None})
        records.sort(key=lambda x:x['last_activity_utc'] or '', reverse=True)
        return {'total':len(records), 'shown':min(250,len(records)), 'sessions':records[:250]}

    # Destructive operations demand BOTH a valid researcher token and a new
    # password check; typing the exact complete session ID prevents row mistakes.
    # Only objects under this exact session prefix are removed. A minimal
    # tombstone outside sessions/ blocks old participant tokens from recreating
    # deleted data during their remaining 72h lifetime.
    @app.delete('/admin/api/sessions/{session_id}', include_in_schema=False)
    async def delete_session(session_id: str, request: Request):
        _require(request)
        if not SID_RE.fullmatch(session_id):
            raise HTTPException(404, 'Session not found')
        raw = await request.body()
        if len(raw) > 1024:
            raise HTTPException(413, 'Request too large')
        try:
            body = json.loads(raw)
            password = body.get('password')
            confirmation = body.get('confirm_session_id')
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(400, 'Invalid deletion request')
        if confirmation != session_id:
            raise HTTPException(400, 'Type the complete session ID to confirm deletion')
        if not isinstance(password, str) or not (1 <= len(password) <= 256):
            raise HTTPException(400, 'Researcher password is required')
        failures = _recent_failures(_ip(request))
        if len(failures) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, 'Too many password attempts. Try again later.')
        try:
            verified = _password.verify(_conf('ADMIN_PASSWORD_HASH'), password)
        except (InvalidHashError, VerifyMismatchError):
            verified = False
        finally:
            password = None
        if not verified:
            failures.append(time.monotonic())
            raise HTTPException(403, 'Incorrect researcher password')
        failures.clear()
        client, storage_bucket = get_s3(), get_bucket()
        prefix = f'sessions/{session_id}/'
        key = f'deleted_sessions/{session_id}.json'
        stored = list(objects(client, storage_bucket, prefix))
        if not stored:
            raise HTTPException(404, 'No stored data for that session')
        # Keep a tiny marker, not a copy of deleted participant data.
        marker = {'session_id':session_id,
                  'deleted_at_utc':datetime.now(timezone.utc).isoformat()}
        client.put_object(Bucket=storage_bucket, Key=key,
                          Body=json.dumps(marker).encode('utf-8'),
                          ContentType='application/json', Metadata={'purpose':'prevent-reupload'})
        deleted = 0
        # Re-list after deletion to catch in-flight uploads. This is not a
        # transaction: unexpected storage errors leave a tombstone in place and
        # remaining objects visible for a later manual retry.
        for attempt in range(3):
            remaining = list(objects(client, storage_bucket, prefix))
            if not remaining:
                break
            for start in range(0, len(remaining), 1000):
                keys = [v['Key'] for v in remaining[start:start+1000]]
                try:
                    result = client.delete_objects(Bucket=storage_bucket,
                        Delete={'Objects':[{'Key':k} for k in keys], 'Quiet':False})
                except Exception as exc:
                    raise HTTPException(502, 'Storage deletion failed; session is protected from further uploads, but some data may remain') from exc
                if result.get('Errors'):
                    raise HTTPException(502, 'Some session objects could not be deleted. The record remains protected from reupload.')
                deleted += len(keys)
        if next(objects(client, storage_bucket, prefix), None) is not None:
            raise HTTPException(502, 'Some objects remain. Session is blocked from new uploads; retry deletion.')
        return JSONResponse({'status':'deleted', 'session_id':session_id,
                             'deleted_objects':deleted}, headers={'Cache-Control':'no-store'})

    # A researcher can download an individual session after signing in.
    @app.get('/admin/api/sessions/{session_id}/download', include_in_schema=False)
    def session_zip(session_id: str, request: Request):
        _require(request)
        if not SID_RE.fullmatch(session_id):
            raise HTTPException(404, 'Session not found')
        try:
            file = build_export(get_s3(), get_bucket(), session_id=session_id, max_size=120*1024*1024)
        except FileNotFoundError:
            raise HTTPException(404, 'Session not found')
        except OverflowError:
            raise HTTPException(413, 'Session too large for browser export')
        return _stream_file(file, f'webgazer_session_{session_id}.zip')

    # The full ZIP has the original records as well as tables and plots.
    @app.get('/admin/api/export-all', include_in_schema=False)
    def export_all(request: Request):
        _require(request)
        try:
            file = build_export(get_s3(), get_bucket(), max_size=350*1024*1024)
        except OverflowError:
            raise HTTPException(413, 'Dataset too large for one browser export. Use the protected CLI export script.')
        return _stream_file(file, 'webgazer_all_sessions_csv_plots_raw.zip')
