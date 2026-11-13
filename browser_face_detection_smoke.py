"""Regression test for face mesh scope and slow callback handling.

Uses simulated face landmarks. This does not validate detection on real webcams.
Run: python browser_face_detection_smoke.py
"""
import asyncio
import pathlib
import re
from playwright.async_api import async_playwright

ROOT=pathlib.Path(__file__).resolve().parent
source=(ROOT/'index.html').read_text()
fake=r'''
window.webgazer={tracker:{model:Promise.resolve(),positionsArray:null,simulateFace:false,
  getEyePatches:async function(){
    if(!this.simulateFace)return false;
    const mesh=Array.from({length:468},()=>[300,200,0]);
    mesh[33]=[230,200,0];mesh[133]=[270,200,0];
    mesh[362]=[370,200,0];mesh[263]=[410,200,0];
    this.positionsArray=mesh;
    return {left:{},right:{}};
  }},getTracker(){return this.tracker},setRegression(){return this},setGazeListener(){return this},
  showVideo(){return this},showFaceOverlay(){return this},showFaceFeedbackBox(){return this},
  showPredictionPoints(){return this},begin(){return this},isReady(){return true},
  removeMouseEventListeners(){return this},setVideoViewerSize(){return this},
  clearData(){return Promise.resolve()},recordScreenPosition(){return this},pause(){return this}};
window.__wgReady=Promise.resolve(true);
'''
source=re.sub(r'<script>\s*/\* We try model files[\s\S]*?</script>',lambda _:f'<script>{fake}</script>',source,count=1)
for lib in ('quality.js','gaze_kinematics.js'):
    source=source.replace(f'<script src="./{lib}"></script>',f'<script>{(ROOT/lib).read_text()}</script>')
source=source.replace('<script src="./deploy-config.js"></script>','')
source=re.sub(r'<script src="\./upload\.js\?[^\"]+"></script>', '''<script>
window.StudyUpload={configured:true,status:{ready:true,submitting:false,receipt:false,stage:'idle',pending:0,sent:0,total:0},
 start:async()=>({session_number:'123456789012'}),enqueue:async()=>true,complete:async()=>({status:'acknowledged'})};
</script>''',source,count=1)
assert 'window.__wgReady=Promise.resolve(true);' in source
source=source.replace('})();\n</script>\n</body>', '''window.__faceSmoke={
 status:()=>({found:faceOK(),gap:faceInfo?.gap,usable:faceTrackerUsableFrames,calls:faceTrackerCalls,responses:faceTrackerResponses,errors:faceTrackerErrors}),
 clear:()=>{lastFaceMs=-1e9;faceInfo=null},
 age:ms=>{lastFaceMs=now()-ms;return faceOK()}
};
})();
</script>
</body>''',1)

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-gpu'])
        page=await browser.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.set_content(source,wait_until='domcontentloaded')
        assert not errors,errors
        await page.wait_for_timeout(2600)
        await page.evaluate('window.__faceSmoke.clear()')
        blank=await page.evaluate('''async()=>{const t=webgazer.getTracker(),c=document.createElement('canvas');c.width=640;c.height=480;await t.getEyePatches(c,640,480);return window.__faceSmoke.status()}''')
        assert not blank['found'] and blank['usable']==0,blank
        good=await page.evaluate('''async()=>{const t=webgazer.getTracker(),c=document.createElement('canvas');c.width=640;c.height=480;t.simulateFace=true;await t.getEyePatches(c,640,480);return window.__faceSmoke.status()}''')
        assert good['found'] and good['usable']==1 and good['gap']>0 and good['errors']==0,good
        assert await page.evaluate('window.__faceSmoke.age(1400)')
        assert await page.evaluate('window.__faceSmoke.age(2400)')
        assert not await page.evaluate('window.__faceSmoke.age(2800)')
        assert not errors,errors
        print('PASS: no camera-only false positive, new FaceMesh landmarks recognized, slow inference grace period')
        await browser.close()

if __name__=='__main__': asyncio.run(main())
