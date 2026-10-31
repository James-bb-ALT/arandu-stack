"""Optional Playwright checks. All API/HA data are FIXTURE_TECNICA, never production.

Run: python tests/browser_redesign_check.py
Requires the existing local playwright package and Chromium; no model downloads.
"""
from pathlib import Path
import json
import mimetypes
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'arandu_stack/app/static'
OUT = ROOT / 'docs/design/screenshots'
MODULES = [('wake', 'Wake', 'Palavra de ativação · OpenWakeWord'),
           ('stt', 'STT', 'Fala para texto · FastConformer'),
           ('nlu', 'NLU', 'Comandos e contexto · Rust'),
           ('decision', 'Decision', 'Intenção e alvo · E5 Small'),
           ('llm', 'LLM', 'Último fallback · Tucano 0.5B'),
           ('tts', 'TTS', 'Texto para fala · Piper PT-BR')]
SCHEMA = [dict(id='threads', label='Threads', type='number', value_type='int',
               min=1, max=4, step=1, section='Execução', help='Fixture técnica.'),
          dict(id='token', label='Token', type='password', configured=True, section='Acesso')]


def fixture_state():
    return dict(local=True, csrf='FIXTURE_TECNICA', version='FIXTURE_TECNICA', phrases=[],
                modules=[dict(id=k, name=n, description=d, installed=True,
                              state='stopped', slug='FIXTURE_TECNICA · processo parado',
                              options={'threads': 2}, schema=SCHEMA) for k, n, d in MODULES])


def watermark(page):
    page.evaluate("""() => {let b=document.createElement('div');b.textContent='FIXTURE TÉCNICA · sem conexão com HA ou satélite';
      b.style='position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#07575a;color:#fff;text-align:center;font:11px system-ui;padding:4px';document.body.append(b)}""")


def no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal page overflow'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1080), color_scheme='dark')
        errors, writes = [], []
        trace_data={'include_text':False,'capacity':50,'requests':[{
            'id':'a'*32,'started_at':'2026-10-31T03:00:00+00:00','received_at':'2026-10-31T03:00:01+00:00',
            'status':'error','origin':'satellite','result':'execution_failed','reason':'backend_unavailable','total_ms':1200,'text':None,
            'stages':{name:{'status':'not_observed'} for name in ['wake','stt','prepare','authorization','queue','nlu','decision','llm','validation','ha','tts']}}]}
        trace_data['requests'][0]['stages'].update(nlu={'status':'accepted','offset_ms':2,'duration_ms':20},
            decision={'status':'disabled'},llm={'status':'not_reached'},validation={'status':'accepted','duration_ms':5},
            ha={'status':'error','duration_ms':1100,'reason':'backend_unavailable'})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request):
            path = urlparse(request.request.url).path
            if path == '/api/state':
                request.fulfill(json=fixture_state()); return
            if path == '/api/logs':
                request.fulfill(json={'version': 'FIXTURE_TECNICA', 'sources': []}); return
            if path == '/api/traces':
                request.fulfill(json=trace_data); return
            if path == '/api/traces/options':
                writes.append((path,request.request.post_data_json))
                trace_data['include_text']=request.request.post_data_json['include_text']
                request.fulfill(json={'saved':True});return
            if path.startswith('/api/'):
                writes.append((path, request.request.post_data_json)); request.fulfill(json={}); return
            if path.startswith('/local_nlu_ai_lab/brand/'):
                file = ROOT / 'custom_components/local_nlu/brand' / Path(path).name
            elif '/static/' in path:
                file = STATIC / path.split('/static/', 1)[1]
            else:
                file = STATIC / 'index.html'
            request.fulfill(path=str(file), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')

        page.route('**/*', route)
        page.goto('http://127.0.0.1:18770/')
        page.locator('#overviewCards .card').nth(5).wait_for()
        watermark(page)
        page.screenshot(path=str(OUT / 'webui-dark-fixture.png'), full_page=True)
        page.locator('#theme').select_option('light')
        page.screenshot(path=str(OUT / 'webui-light-fixture.png'), full_page=True)
        page.locator('nav [data-tab=modules]').click()
        page.locator('#cards .card').first.get_by_text('Configurar', exact=True).click()
        assert page.locator('#setting-wake-token').input_value() == ''
        page.locator('#setting-wake-threads').fill('3')
        page.locator('#saveConfig').click()
        page.locator('#configDialog').wait_for(state='hidden')
        assert writes[-1] == ('/api/modules/wake/options', {'options': {'threads': 3}})
        page.locator('#cards .card').first.get_by_text('Ativar', exact=True).click()
        page.wait_for_function("!document.querySelector('#refresh').disabled")
        assert writes[-1][0] == '/api/modules/wake/start'
        for tab in ('flow', 'lab', 'phrases', 'logs', 'help', 'overview'):
            page.locator(f'nav [data-tab={tab}]').click()
            assert page.locator('#' + tab).is_visible()
        page.locator('nav [data-tab=flow]').click()
        page.locator('#traceSummary').wait_for(state='visible')
        assert 'Erro técnico' in page.locator('#traceSummary').inner_text()
        assert '1,10 s' in page.locator('#traceStages').inner_text()
        assert 'Não observado' in page.locator('#traceStages').inner_text()
        page.locator('#traceAuto').uncheck()
        page.locator('#traceText').check()
        page.wait_for_function("!document.querySelector('#refresh').disabled")
        assert writes[-1] == ('/api/traces/options', {'include_text':True})
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(OUT/'request-flow-fixture.png'), full_page=True)
        for width in (390, 820, 1440):
            page.set_viewport_size(dict(width=width, height=1080))
            for tab in ('overview', 'modules', 'flow', 'phrases', 'logs'):
                page.locator(f'nav [data-tab={tab}]').click(); no_overflow(page)
        page.set_viewport_size(dict(width=390, height=844))
        page.locator('#theme').select_option('dark'); page.locator('nav [data-tab=overview]').click()
        page.screenshot(path=str(OUT / 'webui-mobile-fixture.png'), full_page=True)
        page.emulate_media(reduced_motion='reduce')
        assert page.locator('#refresh').evaluate('e=>getComputedStyle(e).transitionDuration') == '0s'
        assert not errors, errors

        # Native panel mount: real component and DOM, bounded authenticated API fixture.
        page.set_viewport_size(dict(width=1440, height=1080))
        page.set_content('<html lang="pt-BR"><head><title>FIXTURE TÉCNICA · AI Lab</title></head><body style="margin:0"></body></html>')

        def mount(script):
            page.add_script_tag(content=script)
            page.evaluate("""() => {
              window.fixtureCalls=[];let panel=document.createElement('arandu-ai-lab');document.body.append(panel);
              panel.hass={callWS:async ({payload})=>{fixtureCalls.push(payload.command);
                if(payload.command==='status')return {models:[]};
                if(payload.command==='catalog')return {entities:[],areas:[]};
                if(payload.command==='readiness')return {production_policy:{decision:false,llm:false},decision:{calibration_valid:false}};
                if(payload.command==='demo_suite')return {id:'FIXTURE_TECNICA',dataset_hash:'fixture',devices:[],cases:[],quick_ids:[],runners:[]};
                throw Error('Unexpected command '+payload.command);
              }};
            }""")
            page.wait_for_function("document.querySelector('arandu-ai-lab').busy===false")
            watermark(page)

        mount((ROOT / 'custom_components/local_nlu/ai_lab_panel.js').read_text(encoding='utf-8'))
        panel = page.locator('arandu-ai-lab')
        assert not panel.locator('#real').is_checked()
        assert panel.locator('#execute').is_disabled()
        before = page.evaluate('fixtureCalls.slice()')
        for pane in ('compare', 'advanced', 'test'):
            panel.locator(f'[data-lab-tab={pane}]').click()
            assert panel.locator(f'[data-lab-pane={pane}]').first.is_visible()
        assert page.evaluate('fixtureCalls') == before
        assert set(before) == {'status', 'catalog', 'readiness', 'demo_suite'}
        page.evaluate("""() => {
          let panel=document.querySelector('arandu-ai-lab');let original=panel._hass.callWS;
          panel._hass.callWS=async message=>{
            if(message.payload.command!=='run')return original(message);
            fixtureCalls.push('run');return {text:'FIXTURE_TECNICA: ligar a luz',total_ms:null,dry_run:true,
              source:'nlu',operations_validated:true,execution_token:'FIXTURE_TECNICA_TOKEN',
              proposal:{intent:'turn_on',target_entities:['light.fixture']},
              catalog:{entities:[{entity_id:'light.fixture',name:'Luz fixture',area_id:'fixture'}],areas:[{area_id:'fixture',names:['Área fictícia']}]},
              stages:Object.fromEntries(['vad','stt','nlu','decision','llm','validation','ha','tts'].map(n=>[n,{status:n==='nlu'?'executed':'skipped'}]))};
          };
        }""")
        panel.locator('#text').fill('FIXTURE_TECNICA: ligar a luz');panel.locator('#run').click()
        page.wait_for_function("document.querySelector('arandu-ai-lab').last?.source==='nlu'")
        assert panel.locator('#execute').is_disabled()
        assert not panel.locator('#real').is_checked()
        assert 'FIXTURE_TECNICA_TOKEN' not in page.evaluate("localStorage.getItem('arandu-lab-history')")
        panel.locator('#real').check()
        assert panel.locator('#execute').is_enabled()
        panel.locator('#text').fill('FIXTURE_TECNICA: outro comando')
        assert panel.locator('#execute').is_disabled(), 'Editing input must invalidate execution token'
        panel.locator('#real').uncheck()
        for width, theme in ((1440, 'dark'), (1440, 'light'), (390, 'dark')):
            page.set_viewport_size(dict(width=width, height=1080))
            page.evaluate("""theme=>{let s=document.documentElement.style;let dark=theme==='dark';
              s.setProperty('--primary-background-color',dark?'#082c32':'#f7f4ed');s.setProperty('--card-background-color',dark?'#122d31':'#fffdf8');
              s.setProperty('--primary-text-color',dark?'#f5f1e8':'#123b40');s.setProperty('--secondary-text-color',dark?'#b1c7c6':'#526e70');
              s.setProperty('--divider-color',dark?'#345459':'#c4d2ca');s.setProperty('--secondary-background-color',dark?'#183b40':'#e8eee8');}
            """, theme)
            no_overflow(page)
            page.screenshot(path=str(OUT / f'lab-{theme}-{width}-fixture.png'), full_page=True)
        assert not errors, errors
        # Visual vector review at the requested small sizes; decorative assets only.
        page.set_content('<html><body style="background:#f7f4ed;color:#123b40;font:16px system-ui"><h1>Arandu · ativos em 24, 32 e 64 px</h1><p>Interpretação vetorial do PNG fornecido · FIXTURE TÉCNICA</p><div id="sheet"></div></body></html>')
        page.evaluate("""() => {for(let name of ['arandu-icon','wake','stt','nlu','decision','llm','tts']){
          let row=document.createElement('p');row.textContent=name+' ';
          for(let size of [24,32,64]){let img=new Image(size,size);img.src='/local_nlu_ai_lab/brand/'+name+'.svg';img.alt=name;img.style='margin:12px;vertical-align:middle';row.append(img)}document.querySelector('#sheet').append(row)}}""")
        page.set_viewport_size(dict(width=800,height=900))
        page.wait_for_function('[...document.images].every(i=>i.complete && i.naturalWidth>0)')
        page.screenshot(path=str(OUT / 'brand-sizes-fixture.png'), full_page=True)
        browser.close()
    print('PASS: WebUI navigation, module requests, secret preservation, three widths, both themes, reduced motion; Lab tabs, no implicit inference, dry-run default. FIXTURE_TECNICA only.')


if __name__ == '__main__':
    main()
