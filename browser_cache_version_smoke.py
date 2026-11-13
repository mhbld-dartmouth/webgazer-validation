from pathlib import Path
import re
from playwright.sync_api import sync_playwright
files=Path(__file__).resolve().parent
participant=(files/'index.html').read_text()
admin=(files/'backend/admin_dashboard.html').read_text()

def script(html, marker):
    for text in re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',html,flags=re.S):
        if marker in text: return text
    raise RuntimeError(marker+' missing')
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page()
    page.set_content('<html><head><meta name="study-deployment-revision" content="oldrev"></head><body></body></html>')
    page.evaluate('''() => {window.fetch=async()=>new Response(JSON.stringify({revision:'newrev'}),{status:200,headers:{'Content-Type':'application/json'}})}''')
    page.evaluate(script(participant,'Show new deployments on the setup screen'))
    assert page.evaluate('async()=>await window.__studyVersionGuard()') is False
    assert page.locator('#studyUpdateNotice button').text_content()=='Reload latest version'
    page.evaluate('window.__studyVersionSessionStarted()')
    assert page.locator('#studyUpdateNotice').count()==0
    assert page.evaluate('async()=>await window.__studyVersionGuard()') is True
    assert page.locator('#studyUpdateNotice').count()==0
    print('PASS participant version check and active-study protection')
    page2=browser.new_page()
    page2.set_content('<html><head><meta name="study-deployment-revision" content="oldrev"></head><body></body></html>')
    page2.evaluate('''() => {window.fetch=async()=>new Response(JSON.stringify({revision:'newrev'}),{status:200,headers:{'Content-Type':'application/json'}})}''')
    page2.evaluate(script(admin,'Updates are announced, not forced'))
    page2.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page2.wait_for_selector('#adminUpdateNotice',timeout=2500)
    assert page2.locator('#adminUpdateNotice button').text_content()=='Refresh portal'
    assert 'sign in again' in page2.locator('#adminUpdateNotice').text_content().lower()
    print('PASS admin version check and manual refresh banner')
    browser.close()
