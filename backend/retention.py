"""Conservative disposal of expired, never-completed upload records.

The participant token expires 72h after issue. Only sessions with NO completion
receipt and with every object untouched for 96h can be removed. Any presence of
completed.json.gz (including unreadable receipts) protects the entire session.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re

from export_reports import objects

SID_RE = re.compile(r"^[a-f0-9]{32}$")
RETENTION_HOURS = 96

def stale_candidates(client, bucket, *, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=RETENTION_HOURS)
    grouped = defaultdict(list)
    for item in objects(client, bucket, "sessions/"):
        parts = item["Key"].split("/")
        if len(parts) >= 3 and SID_RE.fullmatch(parts[1]):
            grouped[parts[1]].append(item)
    candidates = []
    for sid, items in grouped.items():
        if any(v["Key"] == f"sessions/{sid}/completed.json.gz" for v in items):
            continue
        stamps = [item.get("LastModified") for item in items]
        # Fail closed if any timestamp is missing or timezone-naive.
        if not stamps or any(not isinstance(t, datetime) or t.tzinfo is None for t in stamps):
            continue
        if max(stamps).astimezone(timezone.utc) <= cutoff:
            candidates.append((sid, items))
    return candidates

def cleanup_stale(client, bucket, *, dry_run=True, max_sessions=25, now=None):
    if max_sessions < 1 or max_sessions > 100:
        raise ValueError("max_sessions must be between 1 and 100")
    candidates = stale_candidates(client, bucket, now=now)
    selected = candidates[:max_sessions]
    deleted_sessions = deleted_objects = 0
    failed_sessions = []
    if not dry_run:
        for sid, items in selected:
            # Recheck the receipt immediately before deleting, including broken
            # receipts, to avoid wiping data that may have been submitted.
            try:
                client.head_object(Bucket=bucket, Key=f"sessions/{sid}/completed.json.gz")
                continue
            except Exception as exc:
                code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
                if code not in ('404', 'NoSuchKey', 'NotFound'):
                    failed_sessions.append(sid)
                    continue
            try:
                for item in items:
                    client.delete_object(Bucket=bucket, Key=item['Key'])
                    deleted_objects += 1
                deleted_sessions += 1
            except Exception:
                failed_sessions.append(sid)
    return {'retention_hours':RETENTION_HOURS, 'dry_run':dry_run,
            'eligible_sessions':len(candidates), 'selected_sessions':len(selected),
            'selected_objects':sum(len(items) for _, items in selected),
            'deleted_sessions':deleted_sessions, 'deleted_objects':deleted_objects,
            'failed_sessions':failed_sessions}
