"""Opt-in headless browser proof on isolated local stack only (Playwright)."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    base=os.environ.get('ARANDU_DECISION_STACK_URL','http://127.0.0.1:18731')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context();page=context.new_page()
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('dialog',lambda dialog:dialog.accept())
        page.goto(base,wait_until='networkidle');page.locator('button.nav[data-tab="modules"]').click()
        card=page.locator('#cards article').filter(has=page.get_by_role('heading',name='Decision',exact=True))
        card.get_by_role('button',name='Configurar',exact=True).click()
        page.locator('#setting-decision-model').select_option('minilm-multilingual')
        page.locator('#setting-decision-backend').select_option('cpu')
        page.locator('#saveRestart').click()
        page.locator('#configDialog').wait_for(state='hidden',timeout=180000)
        page.locator('#notice').filter(has_text='Decision aplicado').wait_for(state='visible',timeout=10000)
        assert 'Decision aplicado' in page.locator('#notice').inner_text()
        status=page.request.get(base+'/api/decision/models').json()
        assert status['configured_model']==status['active_model']=='minilm-multilingual'
        card=page.locator('#cards article').filter(has=page.get_by_role('heading',name='Decision',exact=True))
        card.get_by_role('button',name='Configurar',exact=True).click()
        page.locator('#setting-decision-backend').select_option('gpu_intel');page.locator('#saveRestart').click()
        page.locator('#configError').filter(has_text='GPU Intel indisponível').wait_for(state='visible',timeout=180000)
        assert page.locator('#configDialog').is_visible()
        status=page.request.get(base+'/api/decision/models').json()
        assert status['configured_model']==status['active_model']=='minilm-multilingual' and status['backend_used']=='cpu'
        assert not errors,errors
        output={'browser':'Chromium headless, new isolated context','selected_and_loaded':'minilm-multilingual:cpu',
            'gpu_error_visible':True,'rollback_preserved':True,'page_errors':errors,'physical_actions':0}
        Path(os.environ.get('ARANDU_DASHBOARD_OUTPUT','.local-data/phase31/dashboard.json')).write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
        context.close();browser.close();print(json.dumps(output))


if __name__=='__main__':main()
