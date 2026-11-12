"""Opt-in browser proof on the isolated validation stack, never physical HA."""
import json
import os
from pathlib import Path
import time

from playwright.sync_api import sync_playwright


def main():
    base = os.environ.get('ARANDU_DECISION_STACK_URL', 'http://127.0.0.1:18744')
    output = Path(os.environ['ARANDU_DASHBOARD_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto(base, wait_until='networkidle')

        def configure(name):
            page.locator('button.nav[data-tab="modules"]').click()
            card = page.locator('#cards article').filter(has=page.get_by_role('heading', name=name, exact=True))
            card.get_by_role('button', name='Configurar', exact=True).click()

        def apply():
            page.locator('#saveRestart').click()
            page.locator('#configDialog').wait_for(state='hidden', timeout=180000)

        def ready(key):
            deadline = time.monotonic()+90
            while time.monotonic()<deadline:
                row = next(r for r in page.request.get(base+'/api/state').json()['modules'] if r['id']==key)
                if row['runtime']['state']=='ready':
                    return row
                page.wait_for_timeout(500)
            raise AssertionError(row)

        configure('TTS')
        budget = page.locator('#setting-tts-piper_thread_budget')
        assert not budget.is_checked()
        assert page.locator('#setting-tts-cpu_threads').is_disabled()
        budget.check()
        assert page.locator('#setting-tts-cpu_threads').is_enabled()
        budget.uncheck()
        page.locator('#setting-tts-queue_limit').fill('2')
        apply()
        assert ready('tts')['options']['queue_limit']==2
        configure('TTS')
        page.locator('#setting-tts-queue_limit').fill('4')
        apply()
        assert ready('tts')['options']['queue_limit']==4
        configure('STT')
        page.locator('#setting-stt-max_clients').fill('4')
        apply()
        ready('stt')
        assert page.request.get(base+'/api/stt/models').json()['active']['client_limit']==4
        configure('STT')
        page.locator('#setting-stt-max_clients').fill('16')
        apply()
        ready('stt')
        assert page.request.get(base+'/api/stt/models').json()['active']['client_limit']==16

        page.locator('button.nav[data-tab="flow"]').click()
        page.locator('#traceWindow').filter(has_text='p99').wait_for()
        assert '300s' in page.locator('#traceWindow').inner_text()
        queues = page.locator('#traceQueues').inner_text()
        assert all(word in queues for word in ('Decision:', 'LLM:', 'TTS:', 'recusados'))
        traces = page.request.get(base+'/api/traces').json()
        trace = next(r for r in traces['requests'] if r['stages']['llm'].get('model')=='tucano')
        page.locator('#traceSelect').select_option(trace['id'])
        stages = page.locator('#traceStages').inner_text()
        assert all(word in stages for word in ('tucano', 'Carga:', 'inferência:', 'Não observado'))
        with page.expect_download() as download:
            page.locator('#traceMetricsCsv').click()
        download.value.save_as(output/'metrics.csv')
        csv = (output/'metrics.csv').read_text(encoding='utf-8')
        assert csv.startswith('stage,p50_ms,p95_ms,p99_ms,active,waiting,rejected')
        assert all('gate_'+key in csv for key in ('decision', 'llm', 'tts'))
        page.locator('details').filter(has=page.locator('#traceExport')).locator('summary').click()
        with page.expect_download() as download:
            page.locator('#traceExport').click()
        download.value.save_as(output/'request.json')
        exported = json.loads((output/'request.json').read_text(encoding='utf-8'))
        assert exported['text'] is None
        assert exported['stages']['playback']['status']=='not_observed'
        page.screenshot(path=str(output/'flow.png'), full_page=True)
        assert not errors, errors
        result = {'kind':'REAL_BROWSER_ISOLATED_STACK', 'physical_actions':0,
            'tts_budget_default_off':True, 'tts_queue_saved_and_restored':True,
            'stt_clients_applied_and_restored':True, 'actual_llm_trace':'tucano',
            'safe_json_csv_exports':True, 'page_errors':errors}
        (output/'report.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        context.close()
        browser.close()
        print(json.dumps(result))


if __name__=='__main__':
    main()
