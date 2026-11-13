"""Browser check for automatically generated numeric sessions and UTC receipt UI.
No camera recording, real uploads, or stored participant data are used.
"""
import asyncio
import pathlib
import re
from playwright.async_api import async_playwright

base=pathlib.Path(__file__).resolve().parent
src=(base/'index.html').read_text(encoding='utf-8')
FAKE='''window.webgazer={tracker:{model:Promise.resolve(),getEyePatches:async()=>false,positionsArray:null},getTracker(){return this.tracker},setRegression(){return this},setGazeListener(){return this},showVideo(){return this},showFaceOverlay(){return this},showFaceFeedbackBox(){return this},showPredictionPoints(){return this},begin(){return this},isReady(){return true},removeMouseEventListeners(){return this},setVideoViewerSize(){return this},clearData(){return Promise.resolve()},recordScreenPosition(){return this},pause(){return this}};window.__wgReady=Promise.resolve(true);'''
src=re.sub(r'<script>\s*/\* ---- Face-model files[\s\S]*?</script>',f'<script>{FAKE}</script>',src,count=1)
for lib in ('quality.js','gaze_kinematics.js','upload.js'):
    src=re.sub(r'<script src="\./'+re.escape(lib)+r'(?:\?[^"]*)?"></script>', lambda match:'<script>'+(base/lib).read_text(encoding='utf-8')+'</script>',src,count=1)
src=src.replace('<script src="./deploy-config.js"></script>', '<script>window.STUDY_API_URL="";</script>')
src=src.replace('})();\n</script>\n</body>', """window.__audit={getNumber:()=>sessionNumber, getPid:()=>pid, mockReceipt:()=>{
   show('done'); StudyUpload.configured=true; StudyUpload.status.ready=true;
   StudyUpload.complete=async()=>({status:'acknowledged',session_number:sessionNumber,
       submitted_at_utc:'2026-11-13T16:30:00.000Z',partial:false});
   document.querySelector('#submitStudyData').disabled=false;
}};
})();
</script>
</body>""")

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-gpu'])
        page=await browser.new_page(viewport={'width':1440,'height':900})
        failures=[]
        page.on('pageerror',lambda err:failures.append(str(err)))
        await page.set_content(src,wait_until='domcontentloaded')
        assert await page.locator('#participantId').count()==0
        await page.locator('input[name=eyeTrackerExperience][value=no]').check()
        await page.click('#toDemo')
        assert not await page.locator('#p-demo').evaluate('(x)=>x.classList.contains("on")'), 'Initial privacy agreement still required'
        await page.locator('#consentAgree').check()
        await page.click('#toDemo')
        await page.locator('#p-demo.on').wait_for(timeout=5000)
        number=await page.evaluate('window.__audit.getNumber()')
        assert re.fullmatch(r'[0-9]{12}',number), number
        assert await page.evaluate('window.__audit.getPid()===window.__audit.getNumber()'), 'Primary participant label should use numeric session number'
        await page.evaluate('window.__audit.mockReceipt()')
        await page.click('#submitStudyData')
        await page.wait_for_function("document.querySelector('#doneSaved').textContent.includes('(UTC)')")
        message=await page.locator('#doneSaved').inner_text()
        assert number in message and '2026-11-13T16:30:00.000Z' in message, message
        assert not failures,failures
        print('Browser smoke PASS: no participant ID input, random 12-digit session, privacy acknowledgment, UTC receipt displayed.')
        await browser.close()
asyncio.run(main())
