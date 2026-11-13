"""Mock browser test: auto pipeline + 4-quadrant nonoverlap stress.
This deliberately does NOT claim real webcam/face-tracker validation.
"""
import asyncio
import pathlib
import re
from playwright.async_api import async_playwright
BASE = pathlib.Path(__file__).resolve().parent
source = (BASE / 'index.html').read_text()
FAKE = '''
window.webgazer={tracker:{model:Promise.resolve(),getEyePatches:async()=>false,positionsArray:null},
 getTracker(){return this.tracker}, setRegression(){return this},setGazeListener(){return this},
 showVideo(){return this},showFaceOverlay(){return this},showFaceFeedbackBox(){return this},
 showPredictionPoints(){return this},begin(){return this},isReady(){return true},
 removeMouseEventListeners(){return this},setVideoViewerSize(){return this},
 clearData(){return Promise.resolve()},recordScreenPosition(){return this},pause(){return this}};
window.__wgReady=Promise.resolve(true);'''
source = re.sub(r'<script>\s*/\* We try model files[\s\S]*?</script>', f'<script>{FAKE}</script>', source, count=1)
for lib in ('quality.js', 'gaze_kinematics.js'):
    source = re.sub(r'<script src="\./'+re.escape(lib)+r'"></script>',
                    lambda _: '<script>' + (BASE/lib).read_text() + '</script>', source, count=1)
source = re.sub(r'<script src="\./deploy-config.js"></script>', '', source, count=1)
source = re.sub(r'<script src="\./upload.js\?[^\"]+"></script>', '''<script>
window.__submitted=0;window.__uploads=[];
window.StudyUpload={configured:true,status:{ready:true,submitting:false,receipt:false,stage:'idle',pending:0,sent:0,total:0},
  start:async()=>({session_number:'123456789012',visit:'pilot'}),
  enqueue:async(kind,data,build)=>{window.__uploads.push({kind,data,build});return true},
  complete:async()=>{window.__submitted++;window.StudyUpload.status.receipt=true;
   return {status:'acknowledged',session_number:'123456789012',submitted_at_utc:'2026-11-13T17:00:00.000Z',partial:false}}
};
</script>''', source, count=1)
# Tiny durations instead of 2 min dot validation & 25 sec search. No gaze data provided,
# thus both validations will fail quality and trigger 1 recali automatically.
source = source.replace('const CAL_MS=2000, CAL_SETTLE=300, CAL_SAMPLES=40, CAL_ITI=250, RESUME_SETTLE=700;',
                        'const CAL_MS=75, CAL_SETTLE=3, CAL_SAMPLES=40, CAL_ITI=2, RESUME_SETTLE=2;')
source = source.replace('const DWELL=2000, ITI=250, PRUNE=QUALITY.analysisStartMs;',
                        'const DWELL=10, ITI=1, PRUNE=QUALITY.analysisStartMs;')
source = source.replace('const SEARCH_TRIAL_TIMEOUT=25000;', 'const SEARCH_TRIAL_TIMEOUT=10;')
source = source.replace('for(const n of [3,2,1])', 'for(const n of [] )')
source = source.replace('const step=(CAL_MS-CAL_SETTLE)/CAL_SAMPLES;', 'const step=(CAL_MS-CAL_SETTLE)/CAL_SAMPLES;')
source = source.replace('const sampleStep=(CAL_MS-CAL_SETTLE)/CAL_SAMPLES;', 'const sampleStep=(CAL_MS-CAL_SETTLE)/CAL_SAMPLES;')
source = source.replace('if(interrupted || k<12)', 'if(interrupted || k<1)')
# force simulated face presence with stable periodic frames.
source = source.replace('})();\n</script>\n</body>', '''
window.__audit={
  makeTrial, searchGeometry, relativeFaceDrift, gazeTimingStats,
  setFaceForTest:(overrides)=>{faceInfo={...faceInfo,...overrides};},
  start:()=>{
    pid='123456789012';sessionNumber=pid;phase='distance';
    recording=true;trackerOn=true;t0=now();sessionUnixStart=Date.now();
    lastFaceMs=now();faceInfo={cx:.5,cy:.5,gap:.11,turn:1};
    faceAnchor={cx:.5,cy:.5,gap:.11,turn:1};anchorEstCm=60;liveDistCm=60;
    window.__faceKeep=setInterval(()=>{lastFaceMs=now();faceInfo.t=lastFaceMs;},80);
    showCalIntro();
  },
  status:()=>({calibrationAttemptCount,validationRunNumber,searchTrials:searchTrials.length,practiceTrials:practiceTrials.length,
   curStage,submissionStarted,submitted:window.__submitted,validationRuns,calibrationHistory})
};
})();
</script>
</body>''')

async def run():
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,args=['--no-sandbox','--disable-gpu'])
        page=await browser.new_page(viewport={'width':1440,'height':900})
        errors=[]
        page.on('pageerror',lambda exc:errors.append(str(exc)))
        await page.set_content(source, wait_until='domcontentloaded')
        assert not errors,errors
        for dims in ((1440,900),(1366,768),(1920,1080),(1100,700)):
            await page.set_viewport_size({'width':dims[0],'height':dims[1]})
            result=await page.evaluate('''()=>{
              const bad=[];let ok=0;const counts=[];
              for(let t=0;t<150;t++){
                let trial=window.__audit.makeTrial();if(!trial){bad.push('null layout');continue}
                if(trial.stim.length!==18 || trial.targets.length!==2)bad.push('count');
                const sx=trial.geometry.border_w_px,sy=trial.geometry.border_h_px,mdX=trial.geometry.min_gap_x_px,mdY=trial.geometry.min_gap_y_px;
                counts.push([...new Set(trial.stim.map(s=>s.quadrant))].length);
                for(let i=0;i<18;i++)for(let j=i+1;j<18;j++){
                  const a=trial.stim[i],b=trial.stim[j];
                  if(Math.abs(a.x-b.x)<sx+mdX-0.01 && Math.abs(a.y-b.y)<sy+mdY-0.01)bad.push('overlap');
                }
                ok++;
              }
              return {bad:bad.slice(0,5),ok,geometry:window.__audit.searchGeometry(),quadrants:Math.min(...counts)};
            }''')
            assert result['ok']==150 and not result['bad'] and result['quadrants']==4,(dims,result)
            print('Quadrant layouts PASS',dims,'150/150, scale',round(result['geometry']['scale'],3))
        await page.set_viewport_size({'width':1440,'height':900})
        await page.evaluate('window.__audit.start()')
        # A moved screen makes the relative inter-eye span smaller; the same
        # measurement also detects significant head translation and turning.
        neutral = await page.evaluate('window.__audit.relativeFaceDrift()')
        assert not neutral['bad'], neutral
        await page.evaluate('window.__audit.setFaceForTest({gap:.080})')
        displaced = await page.evaluate('window.__audit.relativeFaceDrift()')
        assert displaced['bad'] and displaced['reason']=='distance', displaced
        await page.evaluate('window.__audit.setFaceForTest({gap:.11,cx:.71})')
        translated = await page.evaluate('window.__audit.relativeFaceDrift()')
        assert translated['bad'] and translated['reason']=='position', translated
        await page.evaluate('window.__audit.setFaceForTest({cx:.5,turn:1.8})')
        turned = await page.evaluate('window.__audit.relativeFaceDrift()')
        assert turned['bad'] and turned['reason']=='head_turn', turned
        await page.evaluate('window.__audit.setFaceForTest({turn:1})')
        sample = await page.evaluate('window.__audit.gazeTimingStats([33.3,33.7,33,50])')
        assert abs(sample['inter_sample_mean_ms']-37.5)<.001, sample
        assert abs(sample['effective_sampling_hz']-26.6666666667)<.001, sample
        print('Pose geometry and gaze interval formula PASS')
        await page.get_by_role('button',name='Start calibration').click()
        await page.wait_for_function("window.__audit.status().calibrationAttemptCount>=1", timeout=10000)
        await page.wait_for_function("document.querySelector('#instruct.on') && document.querySelector('#iTitle').textContent.includes('Find the letters')",timeout=45000)
        status=await page.evaluate('window.__audit.status()')
        print('Validation state:',status)
        assert status['calibrationAttemptCount']==2,status
        assert status['validationRunNumber']==2,status
        await page.get_by_role('button',name='Start 10 practice rounds').click()
        await page.wait_for_function("document.querySelector('#instruct.on') && document.querySelector('#iTitle').textContent.includes('Now begin')",timeout=25000)
        await page.get_by_role('button',name='Start 50 study trials').click()
        await page.wait_for_function('window.__submitted===1',timeout=85000)
        status=await page.evaluate('window.__audit.status()')
        assert status['searchTrials']==50 and status['practiceTrials']==10,status
        assert status['submitted']==1,status
        assert await page.locator('#doneSaved').inner_text() == 'Session 123456789012 received at 2026-11-13T17:00:00.000Z (UTC). Your study data were received by the research server. You may close this tab.'
        assert not errors,errors
        print('Full browser auto pipeline PASS',status)
        await browser.close()

asyncio.run(run())
