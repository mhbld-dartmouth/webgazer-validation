import os,sys,time,io,json,gzip,zipfile,secrets,re
from pathlib import Path
from datetime import datetime, timezone
from argon2 import PasswordHasher
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEST_ADMIN_PASSWORD = secrets.token_urlsafe(32)
os.environ.update({
'STUDY_SECRET':secrets.token_urlsafe(48),
'S3_BUCKET':'test-bucket', 'PUBLIC_BASE_URL':'https://study.example.org',
'ADMIN_PASSWORD_HASH':PasswordHasher().hash(TEST_ADMIN_PASSWORD),
'ADMIN_SESSION_SECRET':secrets.token_urlsafe(48),
'S3_ENDPOINT':'https://example.invalid','S3_ACCESS_KEY_ID':secrets.token_urlsafe(24),'S3_SECRET_ACCESS_KEY':secrets.token_urlsafe(48),
})
import main

class MemoryBucket:
 def __init__(self):self.data={};self.transcoding=False
 def head_object(self,Bucket,Key):
  if Key not in self.data:raise ClientError({'Error':{'Code':'404'},'ResponseMetadata':{'HTTPStatusCode':404}},'HeadObject')
  return {'Metadata':self.data[Key]['Metadata'],'Size':len(self.data[Key]['Body'])}
 def list_objects_v2(self,Bucket,Prefix,MaxKeys=1000,ContinuationToken=None):
  keys=sorted(k for k in self.data if k.startswith(Prefix));offset=int(ContinuationToken or 0);portion=keys[offset:offset+MaxKeys]
  result={'Contents':[{'Key':k,'Size':len(self.data[k]['Body']),'LastModified':datetime.now(timezone.utc)} for k in portion]}
  if offset+MaxKeys<len(keys):result['NextContinuationToken']=str(offset+MaxKeys)
  return result
 def put_object(self,Bucket,Key,Body,ContentType,Metadata,ContentEncoding=None):
  self.data[Key]={'Body':Body,'Metadata':Metadata, 'ContentType':ContentType, 'ContentEncoding':ContentEncoding}
 def delete_object(self,Bucket,Key):self.data.pop(Key,None)
 def delete_objects(self,Bucket,Delete):
  deleted=[]
  for entry in Delete['Objects']:
   self.data.pop(entry['Key'],None)
   deleted.append({'Key':entry['Key']})
  return {'Deleted':deleted, 'Errors':[]}
 def get_object(self,Bucket,Key):
  if Key not in self.data:raise KeyError(Key)
  val=self.data[Key]
  body=val['Body']
  # Simulate transparent decompression by Google Cloud Storage for legacy
  # objects labelled with Content-Encoding:gzip.
  if val.get('ContentEncoding')=='gzip' and self.transcoding:
   body=gzip.decompress(body)
  return {'Body':io.BytesIO(body)}
store=MemoryBucket()
main.s3=lambda:store
client=TestClient(main.app,base_url='https://study.example.org')

def open_session():
 r=client.post('/api/public-session',headers={'Origin':'https://study.example.org'})
 assert r.status_code==200,r.text
 data=r.json();return data['session_id'],{'Authorization':'Bearer '+data['token'],'Origin':'https://study.example.org'}

def test_public_page_and_open_session():
 assert client.get('/').status_code==200
 assert client.get('/health').json()['status']=='ok'
 assert client.post('/api/sessions',json={}).status_code==401
 sid,headers=open_session()
 assert len(sid)==32
 signed=main.verify(headers['Authorization'].removeprefix('Bearer '),os.environ['STUDY_SECRET'])
 assert re.fullmatch(r'[0-9]{12}',signed['session_number'])
 ready=client.post('/api/sessions',headers=headers,json={}).json()
 assert ready['session_id']==sid and ready['session_number']==signed['session_number']
 start=json.loads(gzip.decompress(store.data[f'sessions/{sid}/start.json.gz']['Body']))
 assert start['session_number']==signed['session_number'] and start['started_at_utc'].endswith('Z')
 assert client.get('/admin/api/sessions',headers=headers).status_code==401
 assert client.get('/admin/api/export-all',headers=headers).status_code==401
 assert client.get('/admin/api/sessions/'+sid+'/download',headers=headers).status_code==401

def test_upload_dedup_complete_and_separation():
 sid,head=open_session()
 assert client.post('/api/sessions',headers=head,json={}).status_code==200
 base={'build':'v2.6','created_at':'2026-10-20T15:00:00Z','kind':'validation_complete',
       'data':{'gaze_events':[{'t':42,'rawX':101,'rawY':190,'phase':'validation'}],
       'validation_summary':{'accPx':80,'accDeg':1.2,'screenQuality':{'pass':True},
       'results':[{'accPx':120},{'accPx':75}]},'participant_details':{'optional_name':'Test'} }}
 url=f'/api/sessions/{sid}/batches/0'
 consent={**base,'kind':'consent','data':{'agreed':True,'version':'consent-v1','acknowledged_at':'2026-10-20T15:00:00Z'}}
 assert client.post(url,headers=head,json=consent).json()['status']=='stored'
 assert client.post(url,headers=head,json=consent).json()['status']=='already_stored'
 assert client.post(url,headers=head,json={**consent,'data':{'x':4}}).status_code==409
 assert client.post(f'/api/sessions/{sid}/complete',headers=head,json={'last_sequence':0}).status_code==409
 assert client.post(f'/api/sessions/{sid}/batches/1',headers=head,json=base).status_code==200
 finish={**base,'kind':'finish','data':{**base['data'], 'search_trials':[{'trial':1,'hit':True}]}}
 assert client.post(f'/api/sessions/{sid}/batches/2',headers=head,json=finish).status_code==200
 receipt=client.post(f'/api/sessions/{sid}/complete',headers=head,json={'last_sequence':2}).json()
 assert receipt['status']=='acknowledged'
 assert re.fullmatch(r'[0-9]{12}',receipt['session_number'])
 assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z',receipt['submitted_at_utc'])
 completion=json.loads(gzip.decompress(store.data[f'sessions/{sid}/completed.json.gz']['Body']))
 assert completion['session_number']==receipt['session_number']
 assert completion['submitted_at_utc']==receipt['submitted_at_utc']
 retry=client.post(f'/api/sessions/{sid}/complete',headers=head,json={'last_sequence':2}).json()
 assert retry['already_completed'] and retry['submitted_at_utc']==receipt['submitted_at_utc']
 assert client.post(f'/api/sessions/{sid}/batches/3',headers=head,json=base).status_code==409
 other_sid,other_head=open_session()
 assert client.post(f'/api/sessions/{sid}/complete',headers=other_head,json={'last_sequence':2}).status_code==403
 assert client.post(f'/api/sessions/{other_sid}/batches/600',headers=other_head,json=base).status_code==400
 return sid

def test_admin_login_password_only_memory_bearer_and_counts():
 sid=test_upload_dedup_complete_and_separation()
 assert client.get('/admin').status_code==200 # only login shell, no stored study data
 assert client.get('/admin/api/sessions').status_code==401
 assert client.post('/admin/api/login',json={'password':'wrong'}).status_code==401
 r=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD})
 assert r.status_code==200,r.text
 assert not r.headers.get('set-cookie')
 token=r.json()['token'];headers={'Authorization':'Bearer '+token}
 assert client.get('/admin/api/sessions').status_code==401 # no cookies or persistent auth
 data=client.get('/admin/api/sessions',headers=headers).json()
 assert data['counts']['submitted']>=1
 assert data['counts']['submitted']==sum(r['status']=='submitted' for r in data['sessions'])
 assert data['total_sessions']>=1
 assert any(row.get('session_number') and row.get('submitted_at_utc') for row in data['sessions'] if row['status']=='submitted')
 assert client.get('/admin/api/export-all',headers={'Authorization':'Bearer not-valid'}).status_code==401
 r=client.get('/admin/api/export-all',headers=headers)
 assert r.status_code==200,r.text
 with zipfile.ZipFile(io.BytesIO(r.content)) as zipped:
  names=zipped.namelist()
  assert 'study_summary.csv' in names
  summary=zipped.read('study_summary.csv').decode('utf-8-sig')
  assert 'session_number' in summary and 'submitted_at_utc' in summary
  assert any(n.endswith('/csv/gaze_events.csv') for n in names)
  assert any(n.endswith('/plots/gaze_scatter_pixels.png') for n in names)
  assert any(n.endswith('/plots/accuracy_by_point.png') for n in names)
  assert any(n.endswith('.json.gz') for n in names)
  assert 'Test' in zipped.read('sessions/'+sid+'/csv/other_fields.csv').decode('utf-8-sig')
 r=client.get('/admin/api/sessions/'+sid+'/download',headers=headers)
 assert r.status_code==200 and r.content[:2]==b'PK'
 assert client.get('/admin/api/sessions/'+sid+'/download').status_code==401
 assert client.get('/admin/api/invitations',headers=headers).status_code==404
 assert client.post('/admin/api/invitations',headers=headers,json={}).status_code==404

def test_cross_origin_path_restriction_and_expiry():
 sid,h=open_session()
 assert client.post('/api/sessions',headers={**h,'Origin':'https://evil.example'},json={}).status_code==403
 assert client.post('/api/public-session',headers={'Origin':'https://evil.example'}).status_code==403
 assert client.post('/admin/api/login',headers={'Origin':'https://evil.example'},json={}).status_code==403
 assert client.get('/backend/main.py').status_code==404
 assert client.get('/.env').status_code==404
 expired=main.sign({'type':'session','visit':'pilot','sid':sid,'exp':1},os.environ['STUDY_SECRET'])
 assert client.post('/api/sessions',headers={'Authorization':'Bearer '+expired},json={}).status_code==401
 assert client.get('/admin/api/export-all').status_code==401

def test_study_kill_switch_and_startup(monkeypatch):
 monkeypatch.setenv('PUBLIC_STUDY_ENABLED','false')
 assert client.post('/api/public-session').status_code==503
 monkeypatch.setenv('PUBLIC_STUDY_ENABLED','true')
 with TestClient(main.app,base_url='https://study.example.org') as t:
  assert t.get('/health').status_code==200


def test_final_submission_without_consent_gate():
 sid,head=open_session()
 number=main.verify(head['Authorization'].removeprefix('Bearer '),os.environ['STUDY_SECRET'])['session_number']
 assert client.post('/api/sessions',headers=head,json={}).status_code==200
 finish={'build':'v3.3','created_at':'2026-11-13T16:00:00.000Z','kind':'finish',
         'data':{'session_number':number,'search_trials':[{'trial':1,'status':'completed'}]}}
 batch_url=f'/api/sessions/{sid}/batches/0'
 assert client.post(batch_url,headers=head,json=finish).status_code==200
 record=json.loads(gzip.decompress(store.data[f'sessions/{sid}/batches/000000.json.gz']['Body']))
 assert record['session_number']==number and record['kind']=='finish'
 receipt=client.post(f'/api/sessions/{sid}/complete',headers=head,json={'last_sequence':0})
 assert receipt.status_code==200,receipt.text
 assert receipt.json()['session_number']==number
 assert receipt.json()['submitted_at_utc'].endswith('Z')


def test_independent_camera_and_gaze_sampling_exports_multiple_validations():
    """Two calibrations/validations retain separate timing tables and UTC receipt."""
    sid, head = open_session()
    assert client.post('/api/sessions', headers=head, json={}).status_code == 200
    num = main.verify(head['Authorization'].removeprefix('Bearer '), os.environ['STUDY_SECRET'])['session_number']
    def summary(run, camera_fps, gaze_hz):
        return {
            'validation_run_number': run,
            'validation_runs': [{'validation_run_number': 1, 'quality_passed': False},
                                {'validation_run_number': 2, 'quality_passed': True}][:run],
            'accPx': 99-run, 'precPx': 22, 'results': [{'i': 1, 'accPx': 99-run}],
            'sampling_timing': {
                'camera_reported_frame_rate_fps': 30,
                'observed_camera_fps': camera_fps,
                'observed_frame_interval_mean_ms': 1000/camera_fps,
                'inter_sample_mean_ms': 1000/gaze_hz,
                'inter_sample_sd_ms': 2,
                'interval_count': 1, 'effective_sampling_hz': gaze_hz},
            'validation_timing': [{'point': 1, 'inter_sample_mean_ms': 1000/gaze_hz}],
            'validation_intervals': [{'point': 1, 'interval_ms': 1000/gaze_hz,
                                     'previous_session_t_ms': 100, 'current_session_t_ms': 100+1000/gaze_hz}],
            'camera_intervals': [{'interval_ms': 1000/camera_fps,
                                  'previous_session_t_ms': 100, 'current_session_t_ms': 100+1000/camera_fps}]
        }
    records = [('validation_complete', summary(1,29,25)),
               ('validation_complete', summary(2,30,27)),
               ('finish',summary(2,30,27))]
    for seq, (kind, val) in enumerate(records):
        data = {'validation_summary': val, 'session_number': num}
        if kind=='finish':data['search_trials']=[{'trial':i+1} for i in range(50)]
        batch = {'kind':kind,'build':'v3.4','created_at':'2026-11-13T17:00:00.000Z','data':data}
        response=client.post(f'/api/sessions/{sid}/batches/{seq}', headers=head, json=batch)
        assert response.status_code == 200, response.text
    result=client.post(f'/api/sessions/{sid}/complete', headers=head, json={'last_sequence':2})
    assert result.status_code==200,result.text
    receipt=result.json()['submitted_at_utc']
    login=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
    export=client.get(f'/admin/api/sessions/{sid}/download',headers={'Authorization':'Bearer '+login})
    assert export.status_code==200,export.text
    import csv
    with zipfile.ZipFile(io.BytesIO(export.content)) as zipf:
        base=f'sessions/{sid}/csv/'
        def csv_rows(name):
            return list(csv.DictReader(io.StringIO(zipf.read(base+name+'.csv').decode('utf-8-sig'))))
        rates=csv_rows('sampling_summary')
        assert len(rates)==2,rates
        assert [r['validation_run_number'] for r in rates]==['1','2']
        assert [float(r['effective_sampling_hz']) for r in rates]==[25,27]
        assert [float(r['observed_camera_fps']) for r in rates]==[29,30]
        assert all(float(r['camera_reported_frame_rate_fps'])==30 for r in rates)
        assert all(r['submitted_at_utc']==receipt for r in rates)
        assert len(csv_rows('camera_intervals'))==2
        assert len(csv_rows('validation_intervals'))==2
        assert len(csv_rows('validation_timing'))==2
        assert len(csv_rows('validation_points'))==2 # no duplicates from final checkpoint
        assert len(csv_rows('validation_runs'))==2
    print('Independent camera and gaze timestamps, multi-run and UTC export PASS')


def test_gcs_transcoding_legacy_receipt_and_export():
    sid, head = open_session()
    client.post('/api/sessions', headers=head, json={})
    for seq, kind in enumerate(('validation_complete', 'finish')):
        data = {'validation_summary':{'accPx':44, 'precPx':25,
                'screenQuality':{'pass':True}, 'results':[{'accPx':42}]},
                'gaze_events':[{'x':100,'y':150,'t':33.33}]}
        r=client.post(f'/api/sessions/{sid}/batches/{seq}', headers=head,
           json={'kind':kind, 'created_at':'2026-11-13T00:00:00Z',
                 'build':'test', 'data':data})
        assert r.status_code==200,r.text
    receipt=client.post(f'/api/sessions/{sid}/complete', headers=head,
                        json={'last_sequence':1})
    assert receipt.status_code==200
    # All records originally stored as gzip + Content-Encoding, as on GCS before fix.
    for key, val in store.data.items():
        if key.startswith(f'sessions/{sid}/'):
            val['ContentEncoding']='gzip'
    store.transcoding=True
    try:
        login=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
        headers={'Authorization':'Bearer '+login}
        data=client.get('/admin/api/sessions',headers=headers)
        assert data.status_code==200,data.text
        record=next(v for v in data.json()['sessions'] if v['session_id']==sid)
        assert record['status']=='submitted' and record['metrics']['accPx']==44
        assert record['submitted_at_utc']==receipt.json()['submitted_at_utc']
        export=client.get(f'/admin/api/sessions/{sid}/download',headers=headers)
        assert export.status_code==200,export.text
        with zipfile.ZipFile(io.BytesIO(export.content)) as z:
            name=f'sessions/{sid}/raw/completed.json.gz'
            assert json.loads(gzip.decompress(z.read(name)))['submitted_at_utc']==receipt.json()['submitted_at_utc']
        all_export=client.get('/admin/api/export-all',headers=headers)
        assert all_export.status_code==200,all_export.text
    finally:
        store.transcoding=False

def test_stale_upload_cleanup_never_deletes_completed_or_receipt_errors():
    from retention import cleanup_stale
    from datetime import timedelta
    sid, head=open_session()
    client.post('/api/sessions',headers=head,json={})
    stale_time=datetime.now(timezone.utc)-timedelta(hours=120)
    # A second, corrupt completion receipt must still prevent deletion.
    protected, protected_head=open_session()
    client.post('/api/sessions',headers=protected_head,json={})
    key=f'sessions/{protected}/completed.json.gz'
    store.data[key]={'Body':b'bad', 'Metadata':{'kind':'complete'}}
    # One old, genuinely completed submission must be preserved too.
    completed, completed_head=open_session()
    client.post('/api/sessions',headers=completed_head,json={})
    finish={'kind':'finish','created_at':'now','build':'test','data':{}}
    client.post(f'/api/sessions/{completed}/batches/0',headers=completed_head,json=finish)
    client.post(f'/api/sessions/{completed}/complete',headers=completed_head,json={'last_sequence':0})
    # The fake storage must expose correct timestamps for a deterministic cleanup.
    old_keys=set()
    for keep in (sid, protected, completed):
        old_keys.update(k for k in store.data if k.startswith(f'sessions/{keep}/'))
    orig=store.list_objects_v2
    def aged(**kwargs):
        result=orig(**kwargs)
        for v in result['Contents']:
            if v['Key'] in old_keys:
                v['LastModified']=stale_time
        return result
    store.list_objects_v2=aged
    try:
        login=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
        h={'Authorization':'Bearer '+login}
        listing=client.get('/admin/api/sessions',headers=h).json()
        assert all(v['session_id']!=sid for v in listing['sessions'])
        assert any(w['session_id']==protected for w in listing['needs_review'])
        preview=client.post('/admin/api/cleanup-stale?dry_run=true',headers=h)
        assert preview.status_code==200,preview.text
        assert any(k.startswith(f'sessions/{sid}/') for k in store.data)
        result=client.post('/admin/api/cleanup-stale?dry_run=false',headers=h)
        assert result.status_code==200,result.text
        assert result.json()['deleted_sessions']>=1
        assert not any(k.startswith(f'sessions/{sid}/') for k in store.data)
        assert any(k.startswith(f'sessions/{protected}/') for k in store.data)
        assert any(k.startswith(f'sessions/{completed}/') for k in store.data)
        export=client.get('/admin/api/export-all',headers=h)
        assert export.status_code==200,export.text
        with zipfile.ZipFile(io.BytesIO(export.content)) as z:
            assert 'export_errors.csv' in z.namelist()
            assert protected in z.read('export_errors.csv').decode('utf-8-sig')
            assert f'sessions/{completed}/manifest.json' in z.namelist()
    finally:
        store.list_objects_v2=orig


def test_password_confirmed_deletion_submitted_and_replay_protection():
    sid, h = open_session()
    assert client.post('/api/sessions', headers=h, json={}).status_code == 200
    finish={'kind':'finish','build':'test', 'created_at':'2026-11-13T00:00:00Z',
            'data':{'search_trials':[{'trial':i+1} for i in range(50)]}}
    assert client.post(f'/api/sessions/{sid}/batches/0',headers=h,json=finish).status_code==200
    assert client.post(f'/api/sessions/{sid}/complete',headers=h,json={'last_sequence':0}).status_code==200
    token=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
    ah={'Authorization':'Bearer '+token}
    endpoint=f'/admin/api/sessions/{sid}'
    assert client.request('DELETE', endpoint,json={'password':TEST_ADMIN_PASSWORD,'confirm_session_id':sid}).status_code==401
    assert client.request('DELETE', endpoint,headers=ah,json={'password':TEST_ADMIN_PASSWORD,'confirm_session_id':'another'}).status_code==400
    assert client.request('DELETE', endpoint,headers=ah,json={'password':'wrong','confirm_session_id':sid}).status_code==403
    assert client.get('/admin/api/stored-sessions').status_code==401
    assert any(x['session_id']==sid for x in client.get('/admin/api/stored-sessions',headers=ah).json()['sessions'])
    response=client.request('DELETE', endpoint,headers=ah,json={'password':TEST_ADMIN_PASSWORD,'confirm_session_id':sid})
    assert response.status_code==200,response.text
    assert response.json()['deleted_objects']>=3
    assert f'deleted_sessions/{sid}.json' in store.data
    assert not any(k.startswith(f'sessions/{sid}/') for k in store.data)
    assert all(x['session_id']!=sid for x in client.get('/admin/api/sessions',headers=ah).json()['sessions'])
    assert all(x['session_id']!=sid for x in client.get('/admin/api/stored-sessions',headers=ah).json()['sessions'])
    assert client.get(f'/admin/api/sessions/{sid}/download',headers=ah).status_code==404
    assert client.post('/api/sessions',headers=h,json={}).status_code==410
    assert client.post(f'/api/sessions/{sid}/batches/0',headers=h,json=finish).status_code==410
    assert client.post(f'/api/sessions/{sid}/complete',headers=h,json={'last_sequence':0}).status_code==410


def test_researcher_can_delete_unfinished_and_bad_receipt():
    token=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
    ah={'Authorization':'Bearer '+token}
    for receipt_kind in ['unfinished','bad_receipt']:
        sid, h=open_session()
        assert client.post('/api/sessions',headers=h,json={}).status_code==200
        if receipt_kind=='bad_receipt':
            store.data[f'sessions/{sid}/completed.json.gz']={'Body':b'broken', 'Metadata':{'kind':'complete'}}
        inventory=client.get('/admin/api/stored-sessions',headers=ah)
        assert inventory.status_code==200,inventory.text
        record=next(x for x in inventory.json()['sessions'] if x['session_id']==sid)
        assert record['status']==('receipt_needs_review' if receipt_kind=='bad_receipt' else 'unfinished')
        reply=client.request('DELETE', f'/admin/api/sessions/{sid}',headers=ah,
            json={'password':TEST_ADMIN_PASSWORD,'confirm_session_id':sid})
        assert reply.status_code==200,reply.text
        assert not any(k.startswith(f'sessions/{sid}/') for k in store.data)


def test_deletion_rejects_bad_session_path_and_origin():
    token=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
    ah={'Authorization':'Bearer '+token}
    assert client.request('DELETE', '/admin/api/sessions/../../api',headers=ah,json={}).status_code in (400,404,405)
    sid,h=open_session()
    client.post('/api/sessions',headers=h,json={})
    bad_origin=client.request('DELETE', f'/admin/api/sessions/{sid}',headers={**ah,'Origin':'https://other.org'},
            json={'password':TEST_ADMIN_PASSWORD,'confirm_session_id':sid})
    assert bad_origin.status_code==403
    assert any(k.startswith(f'sessions/{sid}/') for k in store.data)


def test_export_recovers_raw_objects_if_processing_fails(monkeypatch):
    """A transformation exception must not silently create an empty export."""
    import export_reports
    sid, head = open_session()
    assert client.post('/api/sessions', headers=head, json={}).status_code == 200
    base = {'kind':'finish', 'build':'regression-test', 'created_at':'2026-11-13T00:00:00Z',
            'data':{'gaze_events':[{'x':5,'y':4,'t':200}], 'search_trials':[{'trial':1}]}}
    assert client.post(f'/api/sessions/{sid}/batches/0', headers=head, json=base).status_code == 200
    assert client.post(f'/api/sessions/{sid}/complete',headers=head,json={'last_sequence':0}).status_code == 200
    token=client.post('/admin/api/login',json={'password':TEST_ADMIN_PASSWORD}).json()['token']
    ah={'Authorization':'Bearer '+token}

    def fail_processing(*args, **kwargs):
        raise RuntimeError('simulated transformation failure')

    with monkeypatch.context() as mp:
        mp.setattr(export_reports, '_session_export', fail_processing)
        for path in (f'/admin/api/sessions/{sid}/download', '/admin/api/export-all'):
            response = client.get(path, headers=ah)
            assert response.status_code == 200, response.text
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                names = z.namelist()
                assert f'sessions/{sid}/RAW_EXPORT_WARNING.txt' in names
                assert f'sessions/{sid}/raw/batches/000000.json.gz' in names
                assert f'sessions/{sid}/raw/completed.json.gz' in names
                assert 'export_errors.csv' in names
                report=z.read('export_errors.csv').decode('utf-8-sig')
                assert sid in report and 'Recovered' in report
                summary=z.read('study_summary.csv').decode('utf-8-sig')
                assert sid in summary and 'raw_only_export_error' in summary
                raw=gzip.decompress(z.read(f'sessions/{sid}/raw/batches/000000.json.gz'))
                assert json.loads(raw)['kind']=='finish'
