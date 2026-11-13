"""Researcher-only CSV, raw JSON and PNG study exports.

Generated on demand; protected admin routes must authorize callers BEFORE this module is called.
Do not use generated files outside approved research data handling procedures.
"""
from __future__ import annotations
import csv
import gzip
import io
import json
import math
import logging
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from storage_codec import decode_json_bytes, ensure_gzip_bytes, read_object_json

logger = logging.getLogger(__name__)



# Read every page of object names, not just the first 1,000 stored files.
def objects(client, bucket, prefix):
    cursor = None
    while True:
        args = {'Bucket': bucket, 'Prefix': prefix, 'MaxKeys': 1000}
        if cursor:
            args['ContinuationToken'] = cursor
        data = client.list_objects_v2(**args)
        yield from data.get('Contents', [])
        cursor = data.get('NextContinuationToken')
        if not cursor:
            return


def read_json(client, bucket, key):
    return read_object_json(client, bucket, key)


def status_for(items):
    keys = {x['Key'].split('/')[-1] for x in items}
    if 'completed.json.gz' in keys:
        return 'submitted'
    return 'not_submitted'



# Free text beginning with spreadsheet formulas is escaped in the CSV exports.
def _safe_csv(v):
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return ''
    if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + v  # avoid spreadsheet formula injection from free-text demographics
    return v


def _csv_content(rows):
    if not rows:
        return b''
    fields = list(dict.fromkeys(k for r in rows for k in r))
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for record in rows:
        writer.writerow({k: _safe_csv(record.get(k)) for k in fields})
    return out.getvalue().encode('utf-8-sig')


def _as_row(value):
    return value if isinstance(value, dict) else {'value': value}


def _png_plot(xs, ys, xlabel, ylabel, title, kind='scatter'):
    if not xs:
        return None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4.6))
    if kind == 'line':
        ax.plot(xs, ys, linewidth=0.9, alpha=0.8)
    elif kind == 'bar':
        ax.bar(xs, ys)
    else:
        ax.scatter(xs, ys, s=5, alpha=0.35)
    ax.set(xlabel=xlabel, ylabel=ylabel, title=title)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    f = io.BytesIO()
    fig.savefig(f, format='png', dpi=140)
    plt.close(fig)
    return f.getvalue()


def _finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None



# Keep the original compressed JSON and make easier-to-analyze tables beside it.
def _session_export(z, s3, bucket, sid, items, prefix=''):
    base = prefix + 'sessions/' + sid + '/'
    collections = defaultdict(list)
    summaries = []
    batches = []
    manifest = {'session_id': sid, 'status': status_for(items), 'generated_at': datetime.now(timezone.utc).isoformat()}
    session_number = submitted_at_utc = None
    # Validation summary is checkpointed more than once; use only the latest
    # copy when constructing one set of timing tables for each session.
    latest_validation_summary = None
    timing_per_run = {}
    for item in sorted(items, key=lambda x: x['Key']):
        key = item['Key']
        if not key.endswith('.json.gz'):
            continue
        data_bytes = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
        z.writestr(base + 'raw/' + key.split('sessions/' + sid + '/', 1)[-1], ensure_gzip_bytes(data_bytes))
        data = decode_json_bytes(data_bytes)
        filename = key.rsplit('/', 1)[-1]
        if filename == 'start.json.gz':
            manifest['start'] = data
            session_number = data.get('session_number')
        elif filename == 'completed.json.gz':
            manifest['completion'] = data
            session_number = data.get('session_number') or session_number
            submitted_at_utc = data.get('submitted_at_utc')
            manifest['status'] = 'incomplete' if data.get('partial') else 'submitted'
        elif '/batches/' in key:
            seq, kind = data.get('sequence'), data.get('kind')
            batches.append({'sequence': seq, 'kind': kind, 'timestamp': data.get('created_at'), 'build': data.get('build')})
            content = data.get('data') or {}
            for field, value in content.items():
                if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                    # Include any future experiment table without changing the exporter.
                    for row in value:
                        collections[field].append({'session_id': sid, 'batch_sequence': seq, **row})
                else:
                    # Every other source field is retained in a typed JSON column.
                    summaries.append({'session_id': sid, 'batch_sequence': seq, 'field': field,
                                      'value': json.dumps(value, ensure_ascii=False)})
                if field == 'validation_summary' and isinstance(value, dict):
                    latest_validation_summary = value
                    run_number = value.get('validation_run_number', 1)
                    # Keep the first complete checkpoint of each assessment, but
                    # prefer the final one where available. Avoid duplicating an
                    # assessment when both finish and validation_complete refer to it.
                    timing_per_run[run_number] = value
                    for point in value.get('results') or []:
                        if isinstance(point, dict):
                            collections['validation_points'].append({'session_id':sid,
                                'batch_sequence':seq, **point})
    if timing_per_run:
        collections['sampling_summary'] = []
        collections['validation_points'] = [
            {'session_id': sid, 'session_number': session_number,
             'validation_run_number': run_number, **row}
            for run_number, summary in sorted(timing_per_run.items())
            for row in (summary.get('results') or []) if isinstance(row, dict)]
        for run_number, summary in sorted(timing_per_run.items()):
            sampling = summary.get('sampling_timing')
            if isinstance(sampling, dict):
                collections['sampling_summary'].append({
                    'session_id': sid, 'session_number': session_number,
                    'submitted_at_utc': submitted_at_utc,
                    'validation_run_number': run_number, **sampling})
            for source in ('validation_timing', 'validation_intervals', 'camera_intervals'):
                rows = summary.get(source)
                if isinstance(rows, list) and rows:
                    collections[source].extend({
                        'session_id': sid, 'session_number': session_number,
                        'submitted_at_utc': submitted_at_utc,
                        'validation_run_number': run_number, **row}
                        for row in rows if isinstance(row, dict))
        runs = (latest_validation_summary or {}).get('validation_runs')
        if isinstance(runs, list):
            collections['validation_runs'] = [
                {'session_id': sid, 'session_number': session_number, **row}
                for row in runs if isinstance(row, dict)]
    for field, rows in collections.items():
        z.writestr(base + 'csv/' + field + '.csv', _csv_content(rows))
    z.writestr(base + 'csv/batches.csv', _csv_content(batches))
    z.writestr(base + 'csv/other_fields.csv', _csv_content(summaries))
    z.writestr(base + 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    sample_rows = collections.get('gaze_events') or []
    gx, gy, gt = [], [], []
    for row in sample_rows[::max(1, len(sample_rows)//15000)]:
        x, y, t = _finite(row.get('rawX', row.get('x'))), _finite(row.get('rawY', row.get('y'))), _finite(row.get('t'))
        if x is not None and y is not None:
            gx.append(x); gy.append(y)
            if t is not None:
                gt.append(t)
    if gx:
        # Screen origin is upper left; visual plot follows the same convention.
        graphic = _png_plot(gx, gy, 'Gaze X (CSS pixels)', 'Gaze Y (CSS pixels)', 'Raw predicted gaze coordinates')
        if graphic:
            z.writestr(base + 'plots/gaze_scatter_pixels.png', graphic)
    if len(gt) == len(gx) and gt:
        graphic = _png_plot([t/1000 for t in gt], gx, 'Elapsed time (seconds)', 'Gaze X (CSS pixels)', 'Predicted horizontal gaze versus time', kind='line')
        if graphic:
            z.writestr(base + 'plots/gaze_x_over_time.png', graphic)
    points = []
    for row in summaries:
        if row['field'] == 'validation_summary':
            try:
                value = json.loads(row['value'])
                if isinstance(value, dict) and isinstance(value.get('results'), list):
                    points = value['results']
            except (ValueError, TypeError):
                pass
    pts_x, pts_y = [], []
    for i, point in enumerate(points):
        if not isinstance(point, dict):
            continue
        err = _finite(point.get('accPx'))
        if err is not None:
            pts_x.append(i+1); pts_y.append(err)
    if pts_x:
        graphic = _png_plot(pts_x, pts_y, 'Validation point', 'Accuracy error (pixels)', 'Error by validation point', kind='line')
        if graphic:
            z.writestr(base + 'plots/accuracy_by_point.png', graphic)
    accuracy = precision = accuracy_deg = precision_deg = None
    for record in reversed(summaries):
        if record['field'] == 'validation_summary':
            try:
                summary = json.loads(record['value'])
                if not isinstance(summary, dict):
                    continue  # Intermediate checkpoints may have validation_summary=null.
                accuracy, precision = summary.get('accPx'), summary.get('precPx')
                accuracy_deg, precision_deg = summary.get('accDeg'), summary.get('precDeg')
                break
            except (TypeError, ValueError): pass
    sampling = (latest_validation_summary or {}).get('sampling_timing') or {}
    return {'session_id': sid, 'session_number': session_number,
            'submitted_at_utc': submitted_at_utc, 'status': manifest['status'],
            'sampling_rate_hz': sampling.get('effective_sampling_hz'),
            'inter_sample_mean_ms': sampling.get('inter_sample_mean_ms'),
            'inter_sample_sd_ms': sampling.get('inter_sample_sd_ms'),
            'sampling_interval_count': sampling.get('interval_count'),
            'camera_reported_frame_rate_fps': sampling.get('camera_reported_frame_rate_fps'),
            'camera_observed_frame_rate_fps': sampling.get('observed_camera_fps'),
            'camera_observed_interval_mean_ms': sampling.get('observed_frame_interval_mean_ms'),
            'accuracy_px':accuracy, 'accuracy_deg':accuracy_deg,
            'precision_px':precision, 'precision_deg':precision_deg,
            'gaze_samples': len(sample_rows),
            'search_trials': len(collections.get('search_trials') or []), 'batches': len(batches),
            'candidate_movements': len(collections.get('events') or [])}



def _recover_raw_session(z, s3, bucket, sid, items):
    """Recover readable objects if transformation fails; never call this a complete export.

    This is intentionally read-only. No live study data is modified or deleted.
    Files are stored as actual gzip archives even if object storage transparently
    decompressed a legacy object. Failures are logged privately, not added to CSV.
    """
    base = f'sessions/{sid}/'
    recovered = 0
    failed = 0
    for item in sorted(items, key=lambda x: x['Key']):
        key = item['Key']
        if not key.endswith('.json.gz') or not key.startswith(base):
            continue
        try:
            payload = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
            z.writestr(base + 'raw/' + key[len(base):], ensure_gzip_bytes(payload))
            recovered += 1
        except Exception:
            failed += 1
            logger.exception('Raw recovery failed: session=%s object=%s', sid, key)
    z.writestr(base + 'RAW_EXPORT_WARNING.txt',
        'The session has a completion receipt, but generating its tabular export failed.\n'
        'These are recovered raw objects, not a verified complete CSV export.\n'
        f'Raw objects recovered: {recovered}; retrieval failures: {failed}.\n'
        'Check Cloud Run logs for the underlying processing exception.\n'
        'Do not delete the stored session until a complete export is verified.\n')
    return {'session_id': sid, 'status': 'raw_only_export_error',
            'raw_objects_recovered': recovered, 'raw_objects_failed': failed}


# One session or the whole study can be exported as a ZIP of raw records, CSVs, and plots.
def build_export(s3, bucket, session_id=None, max_size=350*1024*1024):
    """Export completed, nonpartial sessions only; never export abandoned uploads.

    In an all-sessions export, unreadable completion receipts are listed in a
    conspicuous warning CSV, rather than failing every other valid export.
    """
    items = list(objects(s3, bucket, f'sessions/{session_id}/' if session_id else 'sessions/'))
    by_session = defaultdict(list)
    for item in items:
        parts = item['Key'].split('/')
        if len(parts) >= 3 and len(parts[1]) == 32 and all(c in '0123456789abcdef' for c in parts[1]):
            by_session[parts[1]].append(item)
    if session_id and session_id not in by_session:
        raise FileNotFoundError('Session not found')
    eligible = []
    unreadable = []
    for sid, session_items in sorted(by_session.items()):
        if not any(v['Key'] == f'sessions/{sid}/completed.json.gz' for v in session_items):
            continue
        try:
            receipt = read_json(s3, bucket, f'sessions/{sid}/completed.json.gz')
            if receipt.get('partial'):
                continue
            eligible.append((sid, session_items))
        except Exception:
            unreadable.append({'session_id': sid, 'issue': 'Completion receipt could not be read. Inspect protected storage/logs.'})
    if session_id and not eligible:
        raise FileNotFoundError('Session not confirmed as submitted; verify receipt before export')
    total_bytes = sum(int(x.get('Size', 0)) for _, group in eligible for x in group)
    if total_bytes > max_size:
        raise OverflowError('Dataset is too large for one browser export. Use the command-line export instead.')
    f = tempfile.SpooledTemporaryFile(max_size=12*1024*1024)
    try:
        with zipfile.ZipFile(f, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
            results = []
            for sid, session_items in eligible:
                try:
                    # Stage each complete session independently. One failing session
                    # must not silently corrupt the ZIP or discard another session.
                    staging = io.BytesIO()
                    with zipfile.ZipFile(staging, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as subzip:
                        result = _session_export(subzip, s3, bucket, sid, session_items)
                    with zipfile.ZipFile(io.BytesIO(staging.getvalue())) as subzip:
                        for name in subzip.namelist():
                            z.writestr(name, subzip.read(name))
                    results.append(result)
                except Exception:
                    # Previously this was silently swallowed in all-sessions
                    # exports, resulting in a 0-byte study_summary.csv. Keep the
                    # original exception in private server logs for diagnosis.
                    logger.exception('Session export processing failed for session=%s', sid)
                    recovery_stage = io.BytesIO()
                    with zipfile.ZipFile(recovery_stage, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as recovery_zip:
                        recovery_result = _recover_raw_session(recovery_zip, s3, bucket, sid, session_items)
                    with zipfile.ZipFile(io.BytesIO(recovery_stage.getvalue())) as recovery_zip:
                        for name in recovery_zip.namelist():
                            z.writestr(name, recovery_zip.read(name))
                    results.append(recovery_result)
                    unreadable.append({'session_id': sid,
                        'issue': f'Processed CSV/plot export failed. Recovered {recovery_result["raw_objects_recovered"]} raw records; '
                                 f'{recovery_result["raw_objects_failed"]} raw retrieval failures. Inspect protected server logs.'})
            z.writestr('study_summary.csv', _csv_content(results))
            z.writestr('README_EXPORT.txt',
                'WebGazer validation study export: ONLY confirmed complete submissions.\n'
                'Abandoned, in-progress, and partial sessions are excluded.\n'
                'READ export_errors.csv if present: those sessions were NOT fully processed.\n'
                'For processing errors, sessions/<id>/raw/ contains recovered raw data; review RAW_EXPORT_WARNING.txt.\n'
                'raw/ contains actual gzip-compressed JSON even for legacy Google Cloud files.\n'
                'csv/ contains flattened study tables; plots/ contains descriptive figures.\n'
                'sampling_summary.csv contains independently observed camera FPS and valid gaze Hz.\n'
                'Keep study data on approved protected research storage.\n')
            if unreadable:
                z.writestr('export_errors.csv', _csv_content(unreadable))
            if not session_id and results:
                graphic = _png_plot([1], [len(results)], 'Completed sessions', 'Count',
                                    'Successfully exported submissions', kind='bar')
                if graphic:
                    z.writestr('study_plots/completion_overview.png', graphic)
        f.seek(0)
        return f
    except BaseException:
        f.close()
        raise
