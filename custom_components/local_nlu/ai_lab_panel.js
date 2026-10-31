/* Native Home Assistant panel. Uses the authenticated hass websocket, never stored tokens. */
class AranduAILab extends HTMLElement {
  duration(ms, missing='Indisponível') {
    if (!Number.isFinite(ms)) return missing;
    return ms >= 1000 ? (ms / 1000).toFixed(2).replace('.', ',')+' s' : ms.toFixed(1).replace('.', ',')+' ms';
  }
  set hass(value) { this._hass = value; if (!this.ready) { this.ready = true; this.mount(); } }
  connectedCallback() { if (this._hass && !this.ready) { this.ready = true; this.mount(); } }
  disconnectedCallback() { this.demoStopRequested=true;clearInterval(this.demoTimer);this.stopRecording(); if (this.audioUrl) URL.revokeObjectURL(this.audioUrl); }
  $(id) { return this.shadowRoot.getElementById(id); }
  saved(key, fallback) { try { return JSON.parse(localStorage.getItem('arandu-lab-' + key)) ?? fallback; } catch { return fallback; } }
  store(key, value) { localStorage.setItem('arandu-lab-' + key, JSON.stringify(value)); }
  async call(command, extra = {}) {
    return this._hass.callWS({type: 'local_nlu/ai_lab', payload: {command, worker: this.$('worker').value,
      entry_id: this.$('entry').value || undefined, stt_endpoint: this.$('sttEndpoint').value,
      tts_endpoint: this.$('ttsEndpoint').value, tts_token: this.$('ttsToken').value,
      model: this.$('decision').value, mode: this.$('mode').value, llm_model:this.$('llm').value || 'tucano',
      test_area_id: this.$('testArea').value,
      threshold: Number(this.$('threshold').value), min_margin: Number(this.$('margin').value), ...extra}});
  }
  async task(fn) {
    if (this.busy) return;
    this.busy = true; this.$('status').textContent = 'Processando…'; this.$('status').setAttribute('role', 'status');
    this.shadowRoot.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try { await fn(); this.$('status').textContent = 'Teste finalizado.'; }
    catch (e) { this.$('status').textContent = 'Falha: ' + (e.message || e); this.$('status').setAttribute('role', 'alert'); }
    finally { this.busy = false; this.shadowRoot.querySelectorAll('button').forEach(b => { b.disabled = false; }); this.executionState(); }
  }
  mount() {
    this.attachShadow({mode: 'open'});
    this.shadowRoot.innerHTML = `
      <style>
      :host{display:block;background:var(--primary-background-color,#f4f6f3);color:var(--primary-text-color,#172a27);font:15px/1.5 system-ui;min-height:100vh}*{box-sizing:border-box}
      main{max-width:1200px;margin:auto;padding:32px 24px 70px}header{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:28px}h1{font-size:30px;letter-spacing:-1px;margin:0}h2{font-size:18px;margin:0 0 16px}p{margin:6px 0}.muted{color:var(--secondary-text-color,#5a6d67)}.badge{border:1px solid #7b9f8c;border-radius:20px;padding:5px 12px;font-size:12px;color:var(--primary-text-color,#24503c)}
      .layout{display:grid;grid-template-columns:300px 1fr;gap:20px}.card{background:var(--card-background-color,white);border:1px solid var(--divider-color,#dae1da);border-radius:16px;padding:22px;margin-bottom:18px}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.row>*{flex:1}.row button{flex:initial}
      label{display:block;font-size:13px;margin:12px 0 5px}input,select,textarea{font:inherit;width:100%;border:1px solid var(--divider-color,#cad6cd);background:var(--card-background-color,white);color:inherit;border-radius:8px;padding:10px}textarea{min-height:110px;resize:vertical}input[type=checkbox]{width:auto}button{font:inherit;border:1px solid var(--divider-color,#cad6cd);background:var(--card-background-color,white);color:inherit;border-radius:9px;padding:10px 14px;cursor:pointer}button.primary{background:#215641;color:white;border-color:#215641}button:disabled{opacity:.45;cursor:wait}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #88b5e6;outline-offset:2px}
      #stages{display:flex;gap:6px;flex-wrap:wrap;margin:15px 0}.stage{border:1px solid var(--divider-color,#dae1da);padding:8px;border-radius:8px;flex:1;font-size:12px;min-width:78px}.executed{border-color:#508567;background:#50856718}.error{border-color:#bb6654}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace;max-height:480px;overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:8px;border-bottom:1px solid var(--divider-color,#ddd)}.scroll{overflow:auto}summary{cursor:pointer}#status{min-height:28px;margin:12px 0}audio{width:100%;margin-top:12px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}.metric{padding:12px;background:#5085670e;border-radius:8px}.metric strong{display:block;font-size:20px}#history button{display:block;width:100%;text-align:left;margin-top:8px}small{font-size:12px}a{color:inherit}
      @media(max-width:850px){.layout{grid-template-columns:1fr}main{padding:20px 12px}header{align-items:start}h1{font-size:25px}}
      </style>
      <main><header><div class="lab-brand"><img src="/local_nlu_ai_lab/brand/arandu-icon.svg" width="52" height="52" alt="Símbolo Arandu"><div><p class="muted eyebrow">ARANDU / CONHECIMENTO EM MOVIMENTO</p><h1>AI Decision Lab</h1><p class="muted">Interprete, compare e revise antes de agir.</p></div></div><span class="badge" id="safety" role="status" aria-live="polite">Dry-run ativo</span></header>
      <nav class="lab-tabs" aria-label="Seções do laboratório"><button data-lab-tab="test" aria-current="page">Testar</button><button data-lab-tab="compare">Comparar</button><button data-lab-tab="advanced">Avançado</button></nav>
      <section class="card" id="demoCard"><h2>Testes prontos · casa fictícia</h2>
        <p class="muted">Dataset sintético com dispositivos, aliases, áreas e gabaritos prontos. Os modelos rodam de verdade, somente após seu clique. Nenhuma ação real é permitida nesta suíte.</p>
        <label for="demoSize">Quantidade de casos por modelo</label><select id="demoSize"><option value="quick">Rápida · 8 comandos</option><option value="full">Completa · 16 comandos</option></select>
        <div class="row"><button class="primary" id="demoAll">Rodar tudo</button><button id="demoNlu">Só NLU Rust</button><button id="demoDecision">Todos os Decision Engines</button><button id="demoLlm">Todos os LLMs</button><button id="demoStop" disabled>Parar após este caso</button></div>
        <p id="demoStatus" role="status" aria-live="polite">Carregando apenas os dados da suíte. Nenhum teste executado.</p><progress id="demoProgress" max="1" value="0" style="width:100%"></progress>
        <p class="muted"><small>Execução sequencial. Política fixa da demonstração: confiança &gt;50%, margem 0,08. LLMs recebem recuperação real do BrazEmbed, medida separadamente. Top-1 avalia intenção e alvo nos casos positivos; decisão final também avalia recusas. Erros técnicos contam como erro. Acurácia nesta demonstração não é acurácia geral em PT-BR.</small></p>
        <div id="demoSummary" class="scroll"></div>
        <details><summary>Comandos, gabaritos e casa fictícia</summary><pre id="demoDataset"></pre></details>
        <details open><summary>Resultados por comando · esperado versus obtido</summary><div id="demoRows"></div></details>
        <div class="row"><button id="demoDatasetDownload">Baixar dataset</button><button id="demoJson">Exportar resultados JSON</button><button id="demoCsv">Exportar resultados CSV</button></div>
      </section>
      <div class="layout"><aside>
      <section class="card"><h2>Componentes</h2>
        <p id="layerReadiness" role="status">Verifique os modelos para consultar a pipeline real.</p>
        <label for="stt">STT · FastConformer existente</label><select id="stt"><option value="original">Original</option><option value="adapted">Adaptado (se instalado)</option></select>
        <label for="mode">Modo de interpretação</label><select id="mode"><option value="pipeline">Pipeline com fallbacks</option><option value="nlu">NLU Rust isolado</option><option value="decision">Decision isolado</option><option value="llm">LLM isolado + recuperação</option></select>
        <label for="testArea">Área de origem do teste</label><select id="testArea"><option value="">Padrão da integração / sem contexto</option></select>
        <p id="testAreaStatus" class="muted" role="status">Carregando áreas do catálogo…</p>
        <p class="muted"><small>Texto digitado não traz um satélite. Escolha um cômodo para testar essa origem. Esta seleção não altera a pipeline real.</small></p>
        <label for="decision">Decision Engine</label><select id="decision"><option value="e5-small">Arandu Decision · E5 Small · verificar serviço</option></select>
        <label for="llm">LLM local</label><select id="llm"><option value="tucano">Arandu LLM · Tucano 2 Qwen 0.5B · verificar serviço</option></select>
        <label for="threshold">Confiança calibrada mínima (80% ou mais)</label><input id="threshold" type="number" min="0.8" max="1" step="0.01" value="0.8">
        <label for="margin">Margem mínima</label><input id="margin" type="number" min="0.001" max="1" step="0.001" value="0.08">
        <p class="muted"><small>Cosine similarity não é probabilidade. Sem calibração válida, o motor encaminha ao próximo nível.</small></p>
        <label for="tts">TTS existente</label><select id="tts"><option value="piper">Piper PT-BR</option><option value="pocket">Pocket TTS</option></select>
        <button id="refresh">Verificar modelos</button><button id="load">Pré-carregar encoder</button><button id="unload">Descarregar modelos</button>
        <label for="installModel">Instalação sob demanda (download no worker)</label><select id="installModel"><option value="brazembed">BrazEmbed · MIT</option><option value="e5-small">E5 Small · MIT</option><option value="saracura">Saracura · Apache-2.0</option><option value="jatoba">JATOBÁ · pesos não comerciais</option><option value="qwen">Qwen Q4 · Apache-2.0</option><option value="tucano">Tucano 2 Q4 · Apache-2.0</option></select><button id="install">Baixar pesos selecionados</button>
      </section>
      <details class="card"><summary>Conexões locais</summary>
        <label for="worker">Worker de inferência (opcional)</label><input id="worker" value="" placeholder="Automático pelo endpoint Arandu Stack">
        <label for="sttEndpoint">Web app STT</label><input id="sttEndpoint" value="http://127.0.0.1:8765">
        <label for="ttsEndpoint">Web app TTS</label><input id="ttsEndpoint" value="http://127.0.0.1:8099">
        <label for="ttsToken">Token TTS (opcional; não salvo)</label><input id="ttsToken" type="password" autocomplete="off">
        <label for="entry">Entry ID NLU (se houver várias integrações)</label><input id="entry">
        <p class="muted"><small>Endereços vistos pelo servidor HA. Em máquinas separadas, use o IP privado do serviço. Sem wake word.</small></p>
      </details>
      <section class="card"><h2>Histórico neste navegador</h2><button id="clear">Apagar histórico</button><div id="history"></div></section>
      </aside><div>
      <section class="card"><h2>Comando em português</h2><label for="text">Texto ou transcrição editável</label><textarea id="text" placeholder="Acende a luz perto da televisão."></textarea>
        <div class="row"><button id="record">Gravar microfone</button><button class="primary" id="run">Processar comando</button><button id="repeat">Repetir teste</button></div>
        <label><input type="checkbox" id="autoTts"> Sintetizar resposta ao final pelo TTS existente</label>
        <p id="transcription" class="muted"></p><p id="status" aria-live="polite">Pronto. Nenhuma ação real será executada.</p>
        <label><input type="checkbox" id="real"> Habilitar execução real com revisão de cada proposta</label>
        <button id="execute" disabled>Executar ação revisada (30 s)</button>
      </section>
      <section class="card"><h2>Resultado da interpretação</h2><p id="productionPolicy" class="muted">Modo de teste independente. A política de produção é definida nas opções da integração HACS.</p>
        <h3>Ação final proposta</h3><p id="answer" aria-live="polite">Nenhum teste executado.</p><div id="finalAction"></div>
        <div id="interpretations"></div><div id="stages"></div><div id="metrics" class="metrics"></div>
        <details><summary>Modo avançado · ranking e diagnósticos completos</summary>
        <div class="scroll"><table><thead><tr><th>Posição</th><th>Dispositivo</th><th>Cômodo</th><th>Ação</th><th>Similaridade bruta</th><th>Elegibilidade</th></tr></thead><tbody id="ranking"></tbody></table></div>
        <pre id="output">Nenhum teste executado.</pre></details>
        <div class="row"><button id="speak">Testar resposta TTS</button><button id="json">Exportar JSON</button><button id="csv">Exportar CSV</button></div><audio id="player" controls></audio>
      </section>
      <section class="card"><h2>Suíte de comandos e benchmark</h2><p class="muted">Edite o JSON. Exemplos sem rótulo não entram na acurácia. Informe entidades reais; nunca use resultados do modelo como gabarito.</p>
        <label for="examples">Exemplos editáveis (máximo 20 por lote)</label><textarea id="examples" spellcheck="false" style="min-height:220px"></textarea>
        <label for="compareModels">Motores para comparar</label><select id="compareModels" multiple aria-label="Motores para comparação"></select>
        <p class="muted"><small>Comparação sequencial, mesmo catálogo, sempre dry-run. Selecione Decision isolado para medir os motores sem a resolução do NLU.</small></p>
        <div class="row"><button id="saveExamples">Salvar exemplos localmente</button><button id="batch">Executar lote</button><button id="catalog">Ver catálogo real</button></div><div id="benchmark" class="scroll"></div><pre id="batchOutput"></pre>
      </section>
      <details class="card"><summary>Calibrar com rótulos revisados</summary><p>Forneça {"train": [...], "validation": [...]}, com pelo menos 50 e 30 textos distintos. Campos: text, expected_intent, expected_entities, requires_clarification. Somente calibração validada no mesmo catálogo/modelo pode autorizar Decision.</p>
        <label for="calibration">Dados de calibração</label><textarea id="calibration" spellcheck="false"></textarea><button id="calibrate">Ajustar e validar calibração</button>
      </details></div></div></main>`;
    this.organize();
    const defaults = ['Liga a luz da sala', 'Liga a sala', 'Liga todos os dispositivos da sala', 'Liga o ventilador do Jaime', 'Acende a luz perto da TV', 'Apaga a iluminação principal', 'Desliga tudo', 'Acende a lua', 'Qual a temperatura da sala?', 'Está muito quente aqui'];
    this.$('examples').value = JSON.stringify(this.saved('examples', defaults.map(text => ({text, expected: null, expected_area: null, correct_result: null, notes: 'Rotular com entidades reais antes de medir acurácia.'}))), null, 2);
    const config = this.saved('config', {});
    for (const id of ['worker','sttEndpoint','ttsEndpoint','entry','threshold','margin']) if (config[id] != null) this.$(id).value = config[id];
    this.$('threshold').value = Math.max(.8, Number(this.$('threshold').value) || .8);
    for (const id of ['worker','sttEndpoint','ttsEndpoint','entry','threshold','margin']) this.$(id).onchange = () => {
      this.invalidate();
      this.store('config', Object.fromEntries(['worker','sttEndpoint','ttsEndpoint','entry','threshold','margin'].map(k => [k, this.$(k).value])));
      const policies=this.saved('policies',{});policies[this.$('decision').value||'e5-small']={threshold:this.$('threshold').value,margin:this.$('margin').value};this.store('policies',policies);
    };
    this.$('decision').onchange=()=>{this.invalidate();const policy=this.saved('policies',{})[this.$('decision').value]||{threshold:.8,margin:.08};this.$('threshold').value=Math.max(.8,Number(policy.threshold)||.8);this.$('margin').value=policy.margin;};
    this.$('text').oninput=()=>this.invalidate();this.$('mode').onchange=()=>this.invalidate();this.$('llm').onchange=()=>this.invalidate();
    this.$('testArea').onchange=()=>this.invalidate();
    this.$('run').onclick = () => this.task(() => this.run());
    for(const [id,kind] of [['demoAll','all'],['demoNlu','nlu'],['demoDecision','decision'],['demoLlm','llm']])this.$(id).onclick=()=>this.task(()=>this.runDemo(kind));
    this.$('demoStop').onclick=()=>{this.demoStopRequested=true;this.$('demoStop').disabled=true;this.$('demoStatus').textContent='Parada solicitada. A inferência atual termina antes de interromper a fila.';};
    this.$('demoSize').onchange=()=>this.demoPreview();
    this.$('demoDatasetDownload').onclick=()=>{if(this.demoData)this.demoDownload('json',this.demoData,'arandu-dataset-ficticio');};
    this.$('demoJson').onclick=()=>this.demoDownload('json');this.$('demoCsv').onclick=()=>this.demoDownload('csv');
    this.$('repeat').onclick = () => this.task(async () => { if (!this.lastInput) throw Error('Nenhum teste anterior'); this.$('text').value = this.lastInput.text; await this.run(this.lastInput); });
    this.$('refresh').onclick = () => this.task(() => this.refresh());
    for (const command of ['load','unload','install']) this.$(command).onclick = () => this.task(async () => {
      const r = await this.call(command, command === 'install' ? {model:this.$('installModel').value} : {});
      this.$('advancedOutput').textContent = JSON.stringify(r, null, 2); if(command==='install') await this.refresh();
    });
    this.$('real').onchange = () => { this.$('safety').textContent = this.$('real').checked ? 'Revisão obrigatória' : 'Dry-run ativo'; this.executionState(); };
    this.$('execute').onclick = () => this.task(async () => {
      if (!this.$('real').checked || !this.last?.execution_token) throw Error('Revise e habilite a execução real');
      const token = this.last.execution_token; delete this.last.execution_token;
      const before = performance.now(); const result = await this.call('execute', {token, confirm: true});
      this.last.stages.ha = {status:['success','query_success'].includes(result.code)?'executed':'rejected', latency_ms:performance.now()-before, output:result};
      this.last.execution = result; this.last.dry_run = false; this.render(this.last); this.persist();
    });
    this.$('record').onclick = () => this.recorder ? this.stopRecording() : this.startRecording();
    this.$('speak').onclick = () => this.task(() => this.speak());
    this.$('clear').onclick = () => { this.store('history', []); this.history(); };
    this.$('saveExamples').onclick = () => this.task(async () => { this.store('examples', JSON.parse(this.$('examples').value)); });
    this.$('batch').onclick = () => this.task(async () => {
      const models = [...this.$('compareModels').selectedOptions].map(o=>o.value);
      this.batch = await this.call('compare', {examples:JSON.parse(this.$('examples').value), models});
      this.renderBenchmark(this.batch);
      this.$('batchOutput').textContent = JSON.stringify(this.batch, null, 2);
    });
    this.$('catalog').onclick = () => this.task(async () => { this.$('batchOutput').textContent = JSON.stringify(await this.call('catalog'), null, 2); });
    this.$('calibrate').onclick = () => this.task(async () => { const data = JSON.parse(this.$('calibration').value); this.$('advancedOutput').textContent = JSON.stringify(await this.call('calibrate', data), null, 2); });
    this.$('json').onclick = () => this.download('json'); this.$('csv').onclick = () => this.download('csv');
    this.history(); this.task(() => this.refresh());
  }
  organize() {
    // Move existing controls; preserve IDs, handlers, authenticated calls and tokens.
    const main=this.shadowRoot.querySelector('main'), layout=main.querySelector('.layout');
    layout.dataset.labPane='test';
    this.$('demoCard').dataset.labPane='compare';
    const suite=this.$('examples').closest('section');suite.dataset.labPane='compare';main.append(suite);
    const advanced=document.createElement('section');advanced.className='advanced-layout';advanced.dataset.labPane='advanced';
    advanced.append(this.$('worker').closest('details'),this.$('calibration').closest('details'),this.$('history').closest('section'));
    const parameters=document.createElement('section');parameters.className='card';
    const heading=document.createElement('h2');heading.textContent='Parâmetros e modelos';parameters.append(heading);
    for(const id of ['threshold','margin','load','unload','installModel','install']){
      const field=this.$(id),label=this.shadowRoot.querySelector('label[for="'+id+'"]');
      const note=id==='margin'&&field.nextElementSibling?.tagName==='P'?field.nextElementSibling:null;
      if(label)parameters.append(label);parameters.append(field);if(note)parameters.append(note);
    }
    const output=document.createElement('pre');output.id='advancedOutput';output.setAttribute('aria-live','polite');parameters.append(output);
    advanced.prepend(parameters);
    advanced.append(this.$('ranking').closest('details'));
    main.append(advanced);
    const styles=document.createElement('style');styles.textContent=`
      :host{--arandu-teal:#07575a;--arandu-gold:#d5aa73;--arandu-sand:#f0ddbd;background:var(--primary-background-color,#082c32);color:var(--primary-text-color,#f5f1e8);font-family:Inter,system-ui,sans-serif}
      main{max-width:1440px;padding:32px 32px 64px}.lab-brand{display:flex;align-items:center;gap:16px}.lab-brand img{border-radius:13px;flex:none}.eyebrow{font-size:9px;letter-spacing:1.6px}h1{font-size:30px;font-weight:500;letter-spacing:-1px}h2{font-weight:550;letter-spacing:-.4px}.muted{color:var(--secondary-text-color,#b1c7c6)}
      .card{background:var(--card-background-color,#122d31);border-color:var(--divider-color,#345459);box-shadow:none;border-radius:14px}.layout{grid-template-columns:310px minmax(0,1fr);gap:24px}.layout>*,.advanced-layout>*{min-width:0}
      .lab-tabs{display:flex;gap:8px;position:sticky;top:0;z-index:2;padding:12px 0 20px;background:var(--primary-background-color,#082c32)}.lab-tabs button{border-radius:24px;padding:10px 24px}.lab-tabs [aria-current=page]{background:var(--arandu-teal);color:#f7f4ed;border-color:var(--arandu-teal)}
      button{min-height:44px;transition:border-color .15s,background .15s}button.primary{background:var(--arandu-teal);color:#fff;border-color:var(--arandu-teal)}input,select,textarea,button{background:var(--card-background-color,#122d31);border-color:var(--divider-color,#345459)}.badge{border-color:var(--divider-color,#345459);background:var(--card-background-color,#122d31);color:var(--primary-text-color,#f5f1e8);font-weight:600;white-space:nowrap}
      #safety{position:fixed;right:24px;bottom:20px;z-index:4;box-shadow:0 2px 12px #0002;padding:10px 18px}#execute{border-width:2px}#finalAction{padding:16px 20px;border-left:3px solid var(--arandu-gold);background:var(--secondary-background-color,#17383d);border-radius:0 12px 12px 0;margin:16px 0}#finalAction:empty{display:none}#interpretations{font-size:13px}#interpretations p{padding:6px 0}.stage{border-radius:12px;padding:12px}.stage.executed{background:#50856718}.metric{background:var(--secondary-background-color,#17383d)}pre{padding:16px;background:var(--secondary-background-color,#17383d);border-radius:10px}details summary{min-height:44px;padding:8px 0;font-weight:550}details[open]>summary{margin-bottom:12px}
      .advanced-layout{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}[hidden]{display:none!important}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--primary-color,#d5aa73);outline-offset:3px}input::placeholder,textarea::placeholder{color:var(--secondary-text-color,#b1c7c6);opacity:1}audio{min-height:44px}
      @media(max-width:1100px){.layout{grid-template-columns:270px minmax(0,1fr)}}@media(max-width:850px){main{padding:20px 16px 84px}.layout,.advanced-layout{grid-template-columns:1fr}.layout>aside{order:2}.layout>div{order:1}.lab-brand{gap:12px}.lab-brand img{width:40px;height:40px}.lab-brand .eyebrow{font-size:8px}h1{font-size:25px}header{align-items:start}.lab-tabs button{flex:1;padding:10px 12px}#safety{right:12px;bottom:12px;font-size:11px}.card{padding:18px}.row{gap:8px}.row button{flex:1;min-width:120px}.scroll{max-width:100%}header>.badge{max-width:40%;white-space:normal}}
      @media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
    `;this.shadowRoot.append(styles);
    for(const button of this.shadowRoot.querySelectorAll('[data-lab-tab]'))button.onclick=()=>this.selectPane(button.dataset.labTab);
    this.selectPane('test');
  }
  selectPane(name) {
    for(const pane of this.shadowRoot.querySelectorAll('[data-lab-pane]'))pane.hidden=pane.dataset.labPane!==name;
    for(const button of this.shadowRoot.querySelectorAll('[data-lab-tab]')){
      if(button.dataset.labTab===name)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    }
  }
  async speak() {
    try {
      const text = this.last?.execution?.response_text || this.last?.clarification_question || this.last?.proposal?.reason || 'Ação interpretada em modo de simulação.';
      const r = await this.call('tts', {text, tts_model:this.$('tts').value});
      if (this.audioUrl) URL.revokeObjectURL(this.audioUrl);
      this.audioUrl = URL.createObjectURL(new Blob([Uint8Array.from(atob(r.audio), c=>c.charCodeAt(0))], {type:'audio/wav'}));
      this.$('player').src = this.audioUrl;
      if (this.last) { this.last.stages.tts = {status:'executed', model:r.model, latency_ms:r.latency_ms, first_chunk_ms:r.first_chunk_ms}; this.render(this.last); this.persist(); }
      await this.$('player').play();
    } catch(e) {
      if(this.last){this.last.stages.tts={status:'error',error:e.message||String(e)};this.render(this.last);this.persist();}
      throw new Error('TTS: '+(e.message||String(e))+'. O resultado da interpretação foi preservado.');
    }
  }
  executionState() { this.$('execute').disabled = this.busy || !this.$('real').checked || !this.last?.execution_token;this.$('demoStop').disabled=!this.demoRunning||this.demoStopRequested; }
  invalidate(){if(this.last)delete this.last.execution_token;this.executionState();}
  renderBenchmark(batch) {
    const table = document.createElement('table'); const head = document.createElement('tr');
    for (const label of ['Motor','Acurácia','Erros de alvo','Falsos acionamentos','Rejeição','LLM','p50 / p95 / p99','RSS máximo']) { const th=document.createElement('th');th.textContent=label;head.append(th); }
    table.append(head); const pct=v=>v==null?'Indisponível':(v*100).toFixed(1)+'%';
    for(const [model,data] of Object.entries(batch.models)) {
      const m=data.metrics,row=document.createElement('tr');
      const values=[model,pct(m.accuracy),m.device_errors??'Indisponível',m.false_activations??'Indisponível',pct(m.rejection_rate),pct(m.llm_rate),[50,95,99].map(p=>this.duration(m.latency_ms?.['p'+p],'—')).join(' / '),m.memory_bytes==null?'Indisponível':(m.memory_bytes/1048576).toFixed(0)+' MiB'];
      for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);
    }
    this.$('benchmark').replaceChildren(table);
    const note=document.createElement('p');note.className='muted';note.textContent='Tempos do processamento de texto no servidor; RSS do processo, sem atribuição exclusiva ao modelo. Calibração versus acerto está no JSON reliability. Latência audível ponta a ponta ainda não medida.';this.$('benchmark').append(note);
  }
  async refresh() {
    let result;
    try{result=await this.call('status');}catch(error){result={models:[],error:error.message||String(error)};}
    this.modelStatus=result.models;
    try {
      const catalog=await this.call('catalog'),previous=this.$('testArea').value;
      this.$('testArea').replaceChildren();
      const empty=document.createElement('option');empty.value='';empty.textContent='Padrão da integração / sem contexto';this.$('testArea').append(empty);
      for(const area of catalog.areas || []){const option=document.createElement('option');option.value=area.area_id;option.textContent=area.names?.[0] || area.area_id;this.$('testArea').append(option);}
      if([...this.$('testArea').options].some(o=>o.value===previous))this.$('testArea').value=previous;
      this.$('testAreaStatus').textContent=(catalog.areas || []).length+' áreas disponíveis para simulação.';
    } catch(error) { this.$('testAreaStatus').textContent='Não foi possível carregar as áreas de teste: '+(error.message || error); }
    try {
      const state=await this.call('readiness'),p=state.production_policy,d=state.decision;
      this.$('layerReadiness').textContent='Pipeline real: NLU ativo; Decision '+(p.decision?'habilitado':'desabilitado')+'; LLM '+(p.llm?'habilitado':'desabilitado')+'. Decision: '+(d.calibration_valid===true?'calibração válida; aceitação depende da confiança e margem de cada hipótese':d.calibration_valid===false?'não calibrado; somente diagnóstico':'indisponível; calibração não verificada')+'. Os testes abaixo são independentes.';
    } catch { this.$('layerReadiness').textContent='Não foi possível consultar a política da integração. Testes independentes.'; }
    const fill = (id, kind) => {
      const previous=this.$(id).value;
      this.$(id).replaceChildren();
      for (const model of result.models.filter(m=>m.kind===kind)) {
        const option = document.createElement('option'); option.value = model.id;
        option.textContent = model.name + (model.installed?' · instalado':' · instalação não confirmada') + (model.available ? ' · serviço respondendo' : ' · indisponível: ' + model.reason);
        option.disabled = !model.available; this.$(id).append(option);
      }
      if([...this.$(id).options].some(o=>o.value===previous&&!o.disabled))this.$(id).value=previous;
    };
    fill('decision','decision'); fill('compareModels','decision'); fill('llm','llm');
    if (result.error) this.$('batchOutput').textContent = result.error;
    try{this.demoData=await this.call('demo_suite');this.$('demoDataset').textContent=JSON.stringify(this.demoData,null,2);this.demoPreview();this.renderDemo();}
    catch(error){this.$('demoStatus').textContent='Suíte indisponível: '+(error.message||error);}
  }
  demoCases(){return this.demoData?.cases.filter(c=>this.$('demoSize').value==='full'||this.demoData.quick_ids.includes(c.id))||[];}
  demoPreview(){if(!this.demoData||this.demoRunning)return;this.$('demoStatus').textContent=`Pronto: ${this.demoData.devices.length} dispositivos fictícios, ${this.demoCases().length} comandos por modelo. Clique para executar; nenhuma inferência começou automaticamente.`;}
  async runDemo(kind){
    if(!this.demoData)throw Error('Carregue a suíte pelo botão Verificar modelos.');
    const cases=this.demoCases();
    this.demoRunners=this.demoData.runners.filter(r=>kind==='all'||r.kind===kind);
    this.demoResults=[];this.demoRowsShown=0;this.$('demoRows').replaceChildren();
    this.demoRunning=true;this.demoStopRequested=false;this.demoPlanned=cases.length;this.demoScope=kind;
    this.$('demoStop').disabled=false;this.$('demoSize').disabled=true;
    const planned=cases.length*this.demoRunners.length;let done=0;
    this.$('demoProgress').max=planned;this.$('demoProgress').value=0;this.renderDemo();
    try{
      for(const runner of this.demoRunners){
        for(const sample of cases){
          if(this.demoStopRequested)break;
          this.$('demoStatus').textContent=`${done+1}/${planned} · ${runner.name} · ${sample.text} · aguardando inferência real…`;
          const model=this.modelStatus?.find(m=>m.id===runner.model);
          let result;
          if(runner.kind!=='nlu'&&!model?.available){
            result={runner,case_id:sample.id,text:sample.text,expected:sample.expected,actual:null,skipped:true,error:false,reason:model?.reason||'Modelo não instalado ou worker inacessível',stages:[],model_invocations:0};
          }else{
            const before=performance.now();
            this.demoTimer=setInterval(()=>{this.$('demoStatus').textContent=`${done+1}/${planned} · ${runner.name} · ${sample.text} · ${this.duration(performance.now()-before)}`+(this.demoStopRequested?' · encerrando após este caso':' · rodando…');},250);
            try{result=await this.call('demo_case',{runner_id:runner.id,case_id:sample.id,dataset_hash:this.demoData.dataset_hash});}
            catch(error){result={runner,case_id:sample.id,text:sample.text,expected:sample.expected,actual:null,correct:false,top1_correct:sample.expected.requires_clarification?null:false,error:true,reason:error.message||String(error),stages:[],model_invocations:0,model_ms:null,total_ms:performance.now()-before};}
            finally{clearInterval(this.demoTimer);}
          }
          this.demoResults.push(result);done++;this.$('demoProgress').value=done;this.renderDemo();
        }
        if(this.demoStopRequested)break;
      }
      this.$('demoStatus').textContent=(this.demoStopRequested?'Interrompido':'Finalizado')+` · ${done}/${planned} casos processados. Resultados reais abaixo; erros e indisponibilidades foram preservados.`;
    }finally{this.demoRunning=false;this.$('demoSize').disabled=false;this.$('demoStop').disabled=true;}
  }
  renderDemo(){
    const table=document.createElement('table'),head=document.createElement('tr');
    for(const label of ['Modelo','Casos feitos','Top-1 · positivos','Decisão final','Chamadas / apoio','Etapa média','Carga total','Inferência média','p95 da etapa','Erros']){const th=document.createElement('th');th.textContent=label;head.append(th);}table.append(head);
    const percentage=(count,total)=>total?`${(100*count/total).toFixed(1)}% (${count}/${total})`:'Não medido';
    const number=v=>typeof v==='number'&&Number.isFinite(v);
    const avg=values=>this.duration(values.length?values.reduce((a,b)=>a+b,0)/values.length:null);
    for(const runner of this.demoRunners||this.demoData?.runners||[]){
      const all=(this.demoResults||[]).filter(r=>r.runner.id===runner.id),rows=all.filter(r=>!r.skipped),positive=rows.filter(r=>r.top1_correct!==null&&r.top1_correct!==undefined);
      const times=rows.map(r=>r.model_ms).filter(number).sort((a,b)=>a-b),loads=rows.map(r=>r.load_ms).filter(number),infer=rows.map(r=>r.inference_ms).filter(number);
      const values=[runner.name,`${rows.length}/${this.demoPlanned||this.demoCases().length}`+(all.some(r=>r.skipped)?' · indisponível':''),percentage(positive.filter(r=>r.top1_correct).length,positive.length),percentage(rows.filter(r=>r.correct).length,rows.length),`${rows.reduce((a,r)=>a+(r.model_invocations||0),0)} / ${rows.reduce((a,r)=>a+(r.support_invocations||0),0)}`,avg(times),this.duration(loads.length?loads.reduce((a,b)=>a+b,0):null),avg(infer),this.duration(times.length?times[Math.ceil(times.length*.95)-1]:null),String(rows.filter(r=>r.error).length)];
      const tr=document.createElement('tr');for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}table.append(tr);
    }
    this.$('demoSummary').replaceChildren(table);
    while(this.demoRowsShown<(this.demoResults||[]).length){
      const row=this.demoResults[this.demoRowsShown++],detail=document.createElement('details'),summary=document.createElement('summary');
      const status=row.skipped?'INDISPONÍVEL':row.error?'ERRO TÉCNICO':row.correct?'ACERTO':'DIVERGÊNCIA';
      summary.textContent=`${row.runner.name} · ${row.text} · ${status}`+(number(row.model_ms)?` · ${this.duration(row.model_ms)}`:'');detail.append(summary);
      const comparison=document.createElement('pre');comparison.textContent=JSON.stringify({esperado:row.expected,obtido:row.actual,primeiro_candidato:row.raw_prediction,motivo:row.reason},null,2);detail.append(comparison);
      const trace=document.createElement('details'),title=document.createElement('summary'),json=document.createElement('pre');title.textContent='Tempos, scores, saída completa e erros';json.textContent=JSON.stringify(row,null,2);trace.append(title,json);detail.append(trace);this.$('demoRows').append(detail);
    }
  }
  demoDownload(format,value=null,name='arandu-demo-resultados'){
    if(!value&&!(this.demoResults||[]).length){this.$('demoStatus').textContent='Nenhum resultado ainda. Clique em um dos botões para rodar os testes.';return;}
    const report=value||{synthetic:true,dry_run:true,dataset_id:this.demoData.id,dataset_hash:this.demoData.dataset_hash,scope:this.demoScope,rows:this.demoResults};
    let text=JSON.stringify(report,null,2);
    if(format==='csv'){
      const columns=['modelo','comando','esperado','obtido','acerto_final','acerto_top1','erro','etapa_ms','carga_ms','inferencia_ms'];
      const cell=v=>'"'+String(typeof v==='object'?JSON.stringify(v):v??'').replaceAll('"','""').replace(/^[=+@-]/,"'$&")+'"';
      text=[columns.map(cell).join(','),...this.demoResults.map(r=>[r.runner.name,r.text,r.expected,r.actual,r.correct,r.top1_correct,r.error,r.model_ms,r.load_ms,r.inference_ms].map(cell).join(','))].join('\r\n');
    }
    const url=URL.createObjectURL(new Blob([text],{type:format==='csv'?'text/csv;charset=utf-8':'application/json'}));const link=document.createElement('a');link.href=url;link.download=name+'.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async run(previous) {
    const input = previous || {text:this.$('text').value, model:this.$('decision').value || 'e5-small', llm_model:this.$('llm').value || 'tucano', mode:this.$('mode').value,
      test_area_id:this.$('testArea').value,
      threshold:Number(this.$('threshold').value), min_margin:Number(this.$('margin').value)};
    this.lastInput = structuredClone(input); this.last = await this.call('run', input);
    this.last.input_origin = this.transcription?.text === input.text ? 'transcrito' : 'digitado';
    if (this.transcription && this.transcription.text === input.text) this.last.stages.stt = {status:'executed', ...this.transcription};
    this.render(this.last); this.persist();
    if(this.$('autoTts').checked)await this.speak();
  }
  render(result) {
    const labels = {turn_on:'ligar',turn_off:'desligar',query:'consultar'};
    const statuses = {executed:'executado',accepted:'aceito',rejected:'rejeitado',ambiguous:'aguardando esclarecimento',uncalibrated:'não calibrado',skipped:'não necessário',error:'erro'};
    const stageNames={vad:'VAD',stt:'STT',nlu:'NLU',decision:'Decision',llm:'LLM',validation:'Validação HA',ha:'Home Assistant',response:'Resposta',tts:'TTS'};
    const reasons = {joint_calibration_missing:'Sem calibração válida para este catálogo e modelo. A hipótese não autoriza uma ação.',insufficient_margin:'Hipóteses de ação/dispositivo ficaram próximas. Isso não identifica, sozinho, qual parte está ambígua.',below_threshold:'Confiança calibrada abaixo do mínimo.',joint_calibrated:'Confiança calibrada e margem suficientes.',no_eligible_candidate:'Nenhum candidato elegível.'};
    Object.assign(reasons,{semantic_proposal_rejected:'A proposta contém área, alvo ou ação que não pude validar. Nenhuma ação autorizada.',action_or_target_unresolved:'Dispositivo ou ação precisam de esclarecimento. Nenhuma ação autorizada.',target_context_unresolved:'Não consegui confirmar qual dispositivo corresponde ao pedido. Verifique os candidatos e a área do teste.'});
    const entities = new Map((result.catalog?.entities || []).map(r=>[r.entity_id,r]));
    const areas = new Map((result.catalog?.areas || []).map(a=>[a.area_id,a.names?.[0] || a.area_id]));
    const policy = result.production_policy;
    this.$('productionPolicy').textContent = 'Teste independente; não altera a pipeline real. ' + (policy ? 'Produção: NLU' + (policy.decision?' → Decision':'') + (policy.llm?' → LLM':'') + '. Toggles nas opções HACS; iniciar/parar modelos é um controle separado.' : 'Política de produção não consultada neste resultado.');
    const interpretation = this.$('interpretations'); interpretation.replaceChildren();
    const paragraph = (parent,title,value) => { const p=document.createElement('p'),strong=document.createElement('strong');strong.textContent=title+': ';p.append(strong,document.createTextNode(value));parent.append(p); };
    paragraph(interpretation,'Comando recebido',`${result.text} (${result.input_origin || 'digitado'})`);
    if(result.test_context)paragraph(interpretation,'Origem do teste',result.test_context.origin_area?`${areas.get(result.test_context.origin_area) || result.test_context.origin_area} · ${result.test_context.source==='lab_selection'?'selecionada no Lab':'padrão da integração'}`:'Sem área e sem satélite. Escolha a área de origem para testar esse contexto.');
    for (const name of ['nlu','decision','llm']) {
      const stage=result.stages[name] || {}, output=stage.output || {}, p=output.proposal || output;
      const top=name==='decision'?output.ranking?.[0]:null;
      const targets=top?[top.entity_id]:(p.target_entities || output.operations?.flatMap(op=>op.targets) || []);
      const action=top?.intent || p.intent || output.operations?.[0]?.action || output.command?.action;
      const names=targets.map(id=>entities.get(id)?.name || result.catalog?.entities?.find(r=>r.id===id)?.name || id);
      const state=name==='decision'?(statuses[output.decision_status] || 'sem decisão'):name==='llm'&&p.requires_clarification?'aguardando esclarecimento':statuses[stage.status] || 'não necessário';
      paragraph(interpretation,'O que '+name.toUpperCase()+' entendeu',`${top?'Hipótese: ':''}${labels[action] || action || 'intenção não identificada'}${names.length?' · '+names.join(', '):''} · ${state}. ${reasons[output.reason] || output.reason || stage.reason || p.reason || ''}`);
      if(name==='decision'&&output.analysis){
        const a=output.analysis,actionTop=a.action?.ranking?.[0];
        paragraph(interpretation,'Ação analisada',`${a.action?.text || 'não identificada'} · hipótese: ${labels[actionTop?.intent] || actionTop?.intent || 'nenhuma'}${Number.isFinite(actionTop?.semantic_similarity)?' · similaridade '+actionTop.semantic_similarity.toFixed(4):''}`);
        const candidates=a.device?.candidates || [];
        paragraph(interpretation,'Dispositivo analisado',a.device?.name || (candidates.length?'Ambíguo: '+candidates.map(c=>(c.name || c.entity_id)+' · '+(areas.get(c.area_id) || 'sem cômodo')).join('; '):'nenhum candidato inequívoco'));
        paragraph(interpretation,'Área analisada',`${areas.get(a.area?.area_id) || 'não identificada'} · ${{explicit:'dita no comando',origin:'origem da conversa',unknown:'sem contexto'}[a.area?.source] || 'sem contexto'}`);
      }
    }
    this.$('stages').replaceChildren();
    for (const [name, stage] of Object.entries(result.stages)) {
      const node = document.createElement('details'); node.className = 'stage ' + stage.status;
      const summary=document.createElement('summary');
      let status=statuses[stage.status] || stage.status;
      if(stage.status==='skipped')status=({vad:result.input_origin==='transcrito'?'não medido':'não necessário — entrada de texto',stt:'não necessário — texto digitado',ha:'simulação, ação não enviada',tts:'não solicitado'})[name] || 'não necessário neste modo';
      if(name==='decision'&&stage.output)status=statuses[stage.output.decision_status] || 'respondeu, sem aceitação';
      if(name==='llm'&&stage.output?.proposal?.requires_clarification)status='aguardando esclarecimento';
      if(result.confirmation_required&&name===result.source)status='aguardando confirmação';
      summary.textContent=(stageNames[name] || name)+' · '+status+(stage.latency_ms!=null?` · ${this.duration(stage.latency_ms)}`:'');
      const detail=document.createElement('pre');detail.textContent=JSON.stringify(stage,null,2);node.append(summary,detail);this.$('stages').append(node);
    }
    this.$('answer').textContent = result.execution?.response_text || (result.confirmation_required ? result.clarification_question+' Hipótese aguardando confirmação; nenhuma ação enviada.' : result.operations_validated ? 'A proposta passou pela validação inicial do Home Assistant. '+(result.sensitive?'Exige confirmação de segurança; execução bloqueada no laboratório.':'Não será executada enquanto a simulação estiver ativa.') : result.clarification_question || 'Nenhuma ação autorizada. Tente reformular o comando.');
    const final=this.$('finalAction');final.replaceChildren();
    const proposal=result.proposal || {};
    const ids=result.target_entities || proposal.target_entities || [];
    const action=proposal.intent || proposal.operations?.[0]?.action;
    paragraph(final,'Ação',labels[action] || action || 'nenhuma');
    for(const id of ids){const row=entities.get(id);paragraph(final,'Dispositivo',`${row?.name || id} · ${id} · ${areas.get(row?.area_id) || 'cômodo não informado'}`);}
    paragraph(final,'Camada responsável',result.source?.toUpperCase() || 'nenhuma');
    paragraph(final,'Validação',result.confirmation_required?'hipótese válida, aguardando confirmação na conversa':result.operations_validated?'validada inicialmente; será verificada novamente antes de executar':result.validation_error || 'sem proposta validada');
    const executionAccepted=!result.execution || ['success','query_success'].includes(result.execution.code);
    paragraph(final,'Executaria a ação?',result.operations_validated&&!result.sensitive&&!result.confirmation_required&&executionAccepted?(result.execution?'A execução foi concluída pelo Home Assistant.':'Sim, após revisão explícita; em simulação nenhuma ação é enviada.'):'Não. '+(result.confirmation_required?'Aguarda confirmação.':result.execution?.response_text || ''));
    for(const [name,stage] of Object.entries(result.stages).filter(([,s])=>s.status==='error'))paragraph(final,'Falha em '+name.toUpperCase(),`${stage.error_code || stage.error || 'erro'} · ${stage.phase || 'fase não informada'} · ${stage.reason || ''}. ${name==='tts'?'Falha de áudio; o resultado da interpretação permanece acima.':'Verifique disponibilidade do módulo e os diagnósticos avançados.'}`);
    this.$('output').textContent = JSON.stringify(result, (key, value) => key === 'execution_token' ? undefined : value, 2);
    const de = result.stages.decision.output || {};
    this.$('ranking').replaceChildren();
    for (const [index,item] of (de.ranking || []).entries()) { const entity=entities.get(item.entity_id),row = document.createElement('tr'); for (const text of [index+1,entity?.name || item.entity_id,areas.get(entity?.area_id) || '—',labels[item.intent] || item.intent,item.raw_score.toFixed(4),(index===0?statuses[de.decision_status]:'hipótese ranqueada')+' · '+(reasons[de.reason] || de.reason || 'não autoriza execução')]) { const cell = document.createElement('td'); cell.textContent = text; row.append(cell); } this.$('ranking').append(row); }
    this.$('metrics').replaceChildren();
    const values = [['Total do servidor', this.duration(result.total_ms)],['Confiança calibrada', de.confidence == null ? 'Não calibrado' : (de.confidence*100).toFixed(1)+'%'],['Margem', de.margin == null ? 'Indisponível' : de.margin.toFixed(4)],['Decisão',statuses[de.decision_status] || 'Não chamada']];
    for (const [label,value] of values) { const node = document.createElement('div'); node.className='metric'; const strong=document.createElement('strong'); strong.textContent=value; node.textContent=label; node.append(strong); this.$('metrics').append(node); }
    this.executionState();
  }
  persist() {
    const safe = JSON.parse(JSON.stringify(this.last, (key,value) => key==='execution_token' ? undefined : value));
    const items = this.saved('history', []); items.unshift(safe); this.store('history', items.slice(0,30)); this.history();
  }
  history() { this.$('history').replaceChildren(); for (const item of this.saved('history', [])) { const button=document.createElement('button'); button.textContent=item.text; button.onclick=()=>{this.$('text').value=item.text;this.last=item;this.render(item);this.selectPane('test');};this.$('history').append(button); } }
  async startRecording() {
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw Error('Microfone exige HTTPS ou localhost');
      this.stream = await navigator.mediaDevices.getUserMedia({audio:true});
      this.parts=[]; this.recorder=new MediaRecorder(this.stream);
      this.recorder.ondataavailable=e=>{if(e.data.size)this.parts.push(e.data);};
      this.recorder.onstop=()=>this.task(async()=>{
        const blob=new Blob(this.parts); if(blob.size>2000000)throw Error('Áudio excedeu 2 MB');
        const bytes=new Uint8Array(await blob.arrayBuffer()); let encoded=''; for(let i=0;i<bytes.length;i+=8192) encoded+=String.fromCharCode(...bytes.subarray(i,i+8192));
        const r=await this.call('transcribe',{audio:btoa(encoded), stt_model:this.$('stt').value});
        this.transcription=r;this.$('text').value=r.text;this.$('transcription').textContent=`Transcrição ${r.model} · ${this.duration(r.latency_ms)}. Edite antes de processar.`;
      });
      this.recorder.start();this.$('record').textContent='Parar e transcrever';this.recordTimer=setTimeout(()=>this.stopRecording(),30000);
    } catch(e){this.stream?.getTracks().forEach(t=>t.stop());this.$('status').textContent=e.message;}
  }
  stopRecording(){clearTimeout(this.recordTimer);if(this.recorder?.state==='recording')this.recorder.stop();this.stream?.getTracks().forEach(t=>t.stop());this.recorder=null;if(this.shadowRoot)this.$('record').textContent='Gravar microfone';}
  download(format) {
    const value=this.batch || this.last;if(!value)return;
    let text=JSON.stringify(value,(key,v)=>key==='execution_token'?undefined:v,2);
    if(format==='csv') {
      const rows=this.batch ? Object.entries(this.batch.models).flatMap(([model,data])=>data.rows.map(r=>({model,text:r.result.text,source:r.result.source,total_ms:r.result.total_ms,expected:r.expected,actual:r.actual}))) : [{text:value.text,source:value.source,total_ms:value.total_ms}];
      const keys=Object.keys(rows[0]||{}); const cell=v=>'"'+String(typeof v==='object'?JSON.stringify(v):v??'').replaceAll('"','""').replace(/^[=+@-]/,"'$&")+'"';
      text=[keys.map(cell).join(','),...rows.map(r=>keys.map(k=>cell(r[k])).join(','))].join('\r\n');
    }
    const url=URL.createObjectURL(new Blob([text],{type:format==='json'?'application/json':'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='arandu-ai-lab.'+format;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
}
if (!customElements.get('arandu-ai-lab')) customElements.define('arandu-ai-lab', AranduAILab);
