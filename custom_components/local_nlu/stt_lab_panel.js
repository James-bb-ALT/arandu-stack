/* Native STT view. Audio and transcripts remain in memory until explicitly exported. */
export function mountSTT(panel) {
  const section=panel.$('sttLab');
  section.innerHTML=`<h2>STT / Reconhecimento de voz</h2>
    <p>Grave até 12 segundos ou envie WAV, WebM/Opus, Ogg ou MP4/AAC (até 1,4 MB). Nenhuma ação doméstica será executada.</p>
    <div class="row"><button data-stt="record">Gravar áudio</button><button data-stt="stop" disabled>Parar gravação</button>
    <label>Enviar arquivo WAV/áudio<input data-stt="file" type="file" accept="audio/wav,audio/webm,audio/ogg,audio/mp4,video/mp4"></label></div>
    <p data-stt="capture" role="status" aria-live="polite">Nenhum áudio selecionado.</p><audio data-stt="player" controls></audio>
    <div class="row"><label>Modelo selecionado<select data-stt="model"><option value="fastconformer">Arandu FastConformer</option><option value="whisper_tiny">Whisper Tiny</option><option value="whisper_base">Whisper Base</option><option value="whisper_small">Whisper Small</option><option value="parakeet_ptbr_30s">Parakeet TDT V3 pt-BR 30s FP32</option><option value="parakeet_ptbr_30s_int8">Parakeet TDT V3 pt-BR 30s INT8</option><option value="parakeet_tagarela">Parakeet TAGARELA pt-BR</option><option value="orukeet_int8" selected>Orukeet INT8</option><option value="granite_turboctc_q4">Granite TurboCTC Q4_K_M</option><option value="parakeet_ultra_q8">Parakeet Ultra Q8_0</option><option value="parakeet_ultra_q4">Parakeet Ultra Q4_K_M</option><option value="phonon_2_5">Phonon 2.5</option><option value="nemotron_unified_0_6b">Nemotron Unified ASR 0.6B</option><option value="whisper_cpp_small">Whisper Small — whisper.cpp (FP16)</option><option value="whisper_cpp_small_q8_0">Whisper Small Q8_0 — whisper.cpp</option><option value="whisper_cpp_small_q5_1">Whisper Small Q5_1 — whisper.cpp</option><option value="whisper_cpp_medium">Whisper Medium — whisper.cpp (FP16)</option><option value="whisper_cpp_medium_q8_0">Whisper Medium Q8_0 — whisper.cpp</option><option value="whisper_cpp_medium_q5_0">Whisper Medium Q5_0 — whisper.cpp</option></select></label>
    <span data-stt="languageBadge">Idioma: Português — fixo</span>
    <label>Variante FastConformer<select data-stt="variant"><option value="adapted">Adaptado</option><option value="original">Original</option></select></label>
    <label>Threads<select data-stt="threads"><option>1</option><option selected>2</option><option>3</option><option>4</option></select></label>
    <label>Repetições<select data-stt="repetitions"><option value="1" selected>1x</option><option value="3">3x (warm)</option><option value="5">5x (warm)</option><option value="10">10x (warm)</option></select></label>
    <label>Ordenar comparação<select data-stt="sort"><option value="order" selected>Ordem de execução</option><option value="wer">Menor WER</option><option value="cer">Menor CER</option><option value="p50">Menor latência P50</option><option value="p95">Menor latência P95</option><option value="ram">Menor RAM</option></select></label>
    <label>Correção neste teste<select data-stt="profile"><option value="">Padrão do motor</option><option value="raw">RAW</option><option value="normalize_only">Somente normalização</option><option value="full">Completa</option></select></label>
    <label>Perfil Nemotron<select data-stt="nemotronProfile"><option value="offline">Offline</option><option value="precision">Precisão (560/560ms)</option><option value="balanced">Balanceado (160/400ms)</option><option value="fast">Rápido (80/160ms)</option><option value="experimental">Experimental (80/80ms)</option></select></label></div>
    <fieldset><legend>Execução e desempenho STT</legend>
    <div class="row">
      <label>Modo de execução<select data-stt="executionMode"><option value="warm" selected>Modelo residente (Warm)</option><option value="cold">Carga fria (Cold)</option><option value="warmup_benchmark">Aquecimento + benchmark</option></select></label>
      <label><input type="checkbox" data-stt="resident" checked> Manter residente em RAM</label>
      <label>Retenção em RAM<select data-stt="retention"><option value="0">Descarregar após teste</option><option value="300">5 minutos</option><option value="900" selected>15 minutos</option><option value="1800">30 minutos</option><option value="permanent">Permanente até descarregar</option></select></label>
      <label>Política de memória<select data-stt="memoryPolicy"><option value="auto" selected>Automática (80% RAM livre)</option><option value="custom">Limite manual (MiB)</option><option value="none">Sem limite interno</option></select></label>
      <label>Limite manual (MiB)<input type="number" data-stt="memoryLimit" min="256" max="65536" step="256" value="4608" disabled></label>
    </div>
    <div class="row">
      <button type="button" data-stt="load">Carregar modelo</button>
      <button type="button" data-stt="warmup">Aquecer modelo</button>
      <button type="button" data-stt="unload">Descarregar modelo</button>
    </div>
    <p data-stt="sessionStatus">Sessão residente: verificando estado no servidor…</p>
    </fieldset>
    <fieldset><legend>Processamento acústico (somente teste no Lab)</legend>
    <div class="row"><label>Preset<select data-stt="preset"><option value="original" selected>Original (sem filtro)</option><option value="vad">VAD (Silero v6.2)</option><option value="balanced">Balanceado (HPF + NS + VAD)</option><option value="custom">Personalizado</option></select></label>
    <label><input type="checkbox" data-stt="vadEnabled"> Silero VAD</label>
    <label><input type="checkbox" data-stt="hpfEnabled"> High-pass</label>
    <label><input type="checkbox" data-stt="nsEnabled"> Noise Suppression</label>
    <label>NS nível<select data-stt="nsLevel"><option value="0">0</option><option value="1" selected>1</option><option value="2">2</option><option value="3">3</option></select></label>
    <label><input type="checkbox" data-stt="agcEnabled"> AGC2</label>
    <label>Threshold VAD<input type="number" data-stt="vadThreshold" value="0.50" min="0.1" max="0.95" step="0.05"></label>
    <label>Pre-roll (ms)<input type="number" data-stt="vadPreRoll" value="300" min="0" max="1000" step="50"></label>
    <label>Min silence (ms)<input type="number" data-stt="vadMinSilence" value="450" min="100" max="2000" step="50"></label></div>
    <p data-stt="processedMeta"></p><audio data-stt="processedPlayer" controls></audio></fieldset>
    <label>Texto que foi falado (opcional)<input data-stt="reference" maxlength="512"></label>
    <label>Entidade falada (opcional; trecho exato para avaliar o alvo)<input data-stt="entityReference" maxlength="128"></label>
    <fieldset><legend>Modelos para comparação sequencial</legend><div data-stt="models"></div></fieldset>
    <div class="row"><button data-stt="refresh">Verificar modelos</button><button data-stt="install">Baixar modelo selecionado</button><button data-stt="remove">Remover modelo selecionado</button>
    <button class="primary" data-stt="test">Transcrever com modelo selecionado</button><button data-stt="compare">Comparar modelos</button><button data-stt="cancel" disabled>Cancelar teste</button></div>
    <p data-stt="status" role="status" aria-live="polite">Configure o endpoint Arandu Stack na integração para conectar.</p>
    <progress data-stt="downloadProgress" max="100" hidden style="width:100%"></progress>
    <p data-stt="active"></p><div data-stt="inventory"></div><div data-stt="results" class="scroll"></div>
    <p data-stt="summary"></p><div class="row"><button data-stt="apply">Usar este modelo no Assist</button><button data-stt="json">Exportar JSON</button>
    <button data-stt="csv">Exportar CSV</button><button data-stt="clear">Limpar histórico de testes</button></div>`;
  const $=id=>section.querySelector(`[data-stt="${id}"]`);
  let blob=null,url=null,stream=null,recorder=null,timer=null,ticker=null,disposed=false,busy=false,recording=false,stoppedAt=null,rows=[],last=null,inventory=null,poll=null;
  const names={fastconformer:'Arandu FastConformer',whisper_tiny:'Whisper Tiny',whisper_base:'Whisper Base',whisper_small:'Whisper Small',parakeet_ptbr_30s:'Parakeet TDT V3 pt-BR 30s FP32',parakeet_ptbr_30s_int8:'Parakeet TDT V3 pt-BR 30s INT8',parakeet_tagarela:'Parakeet TAGARELA pt-BR',orukeet_int8:'Orukeet INT8',granite_turboctc_q4:'Granite TurboCTC Q4_K_M',parakeet_ultra_q8:'Parakeet Ultra Q8_0',parakeet_ultra_q4:'Parakeet Ultra Q4_K_M',phonon_2_5:'Phonon 2.5',nemotron_unified_0_6b:'Nemotron Unified ASR 0.6B',whisper_cpp_small:'Whisper Small — whisper.cpp (FP16)',whisper_cpp_small_q8_0:'Whisper Small Q8_0 — whisper.cpp',whisper_cpp_small_q5_1:'Whisper Small Q5_1 — whisper.cpp',whisper_cpp_medium:'Whisper Medium — whisper.cpp (FP16)',whisper_cpp_medium_q8_0:'Whisper Medium Q8_0 — whisper.cpp',whisper_cpp_medium_q5_0:'Whisper Medium Q5_0 — whisper.cpp'};
  const states={not_installed:'Não instalado',downloading:'Baixando',verifying:'Verificando',installed:'Instalado',loading:'Carregando',ready:'Pronto',cancelled:'Cancelado',error:'Erro',runtime_missing:'Runtime ausente',dependency_missing:'Dependência ausente',dependency_conflict:'Conflito de dependências',runtime_ready:'Runtime OK',model_not_installed:'Pesos não baixados',model_corrupted:'Pesos corrompidos',model_load_failed:'Falha na carga',model_ready:'Modelo pronto',oom:'Sem memória (OOM)',timeout:'Tempo limite excedido'};
  for(const [id,name] of Object.entries(names)){
    const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=id;input.checked=['fastconformer','whisper_base'].includes(id);
    label.append(input,document.createTextNode(' '+name));$('models').append(label);
  }
  function status(text,error=false){$('status').textContent=text;$('status').setAttribute('role',error?'alert':'status');}
  function release(){clearTimeout(timer);clearInterval(ticker);stream?.getTracks().forEach(track=>track.stop());stream=null;}
  function syncInstallButton(){
    const btn=$('install');if(!btn)return;
    const selected=inventory?.models?.find(m=>m.id===$('model')?.value);
    const needsReinstall=Boolean(selected&&(selected.installed||selected.incomplete||selected.weights_status==='model_corrupted'||selected.state==='error'));
    btn.textContent=needsReinstall?'Verificar / reinstalar':'Baixar modelo selecionado';
    const isActiveOp=Boolean(selected&&['downloading','verifying'].includes(selected.state));
    btn.disabled=busy||recording||isActiveOp;
  }
  function syncMemoryControls(){
    const ml=$('memoryLimit'),mp=$('memoryPolicy');
    if(ml&&mp)ml.disabled=busy||recording||mp.value!=='custom';
  }
  function renderSession(session){
    const box=$('sessionStatus');if(!box)return;
    if(!session||typeof session!=='object'||!session.state){box.textContent='Sessão residente: indisponível ou não consultada.';return;}
    const stMap={unloaded:'Descarregado',loading:'Carregando',resident:'Residente em RAM',warming:'Aquecendo',ready:'Pronto (aquecido)',inferring:'Executando inferência',error:'Erro na sessão'};
    const mb=session.memory_budget||{};
    const memInfo=typeof mb.effective_available_mb==='number'?` · RAM efetiva livre ${mb.effective_available_mb.toFixed(0)} MiB · limite ${mb.memory_limit_mb!=null?mb.memory_limit_mb.toFixed(0)+' MiB':'sem limite'} (${mb.memory_policy||'auto'})`:'';
    if(session.model){
      const rss=typeof session.process_rss_mb==='number'?`${session.process_rss_mb.toFixed(1)} MiB`:'N/D';
      const cold=typeof session.cold_load_ms==='number'?`${session.cold_load_ms.toFixed(0)} ms`:'—';
      const warm=session.warmed_up?` · aquecido (${typeof session.warmup_ms==='number'?session.warmup_ms.toFixed(0)+' ms':'OK'})`:' · sem aquecimento';
      const ret=session.retention_seconds==null?'permanente':`${session.retention_seconds}s`;
      box.textContent=`Sessão Lab: ${stMap[session.state]||session.state} · ${names[session.model]||session.model} · RSS ${rss} · carga fria ${cold}${warm} · inferências ${session.infer_count||0} · retenção ${ret}${memInfo}${session.last_error?' · erro: '+session.last_error:''}`;
    }else{
      box.textContent=`Sessão Lab: ${stMap[session.state]||session.state} · nenhum modelo residente${memInfo}${session.last_error?' · erro: '+session.last_error:''}`;
    }
  }
  function sessionPayload(){
    const mp=$('memoryPolicy')?.value||'auto';
    const retVal=$('retention')?.value||'900';
    return {
      stt_model:$('model').value,
      variant:$('variant').value,
      threads:Number($('threads').value),
      memory_policy:mp,
      memory_limit_mb:mp==='custom'?Number($('memoryLimit')?.value||4608):null,
      retention_seconds:retVal==='permanent'?null:Number(retVal),
      nemotron_streaming_profile:$('nemotronProfile')?.value||'offline',
    };
  }
  function controls(){
    section.querySelectorAll('button,input,select').forEach(node=>node.disabled=busy||recording);
    $('stop').disabled=!recording;
    if($('cancel'))$('cancel').disabled=!busy;
    if($('sort')&&!recording)$('sort').disabled=false;
    syncInstallButton();
    syncMemoryControls();
  }
  async function task(fn){
    if(busy||recording)return;
    busy=true;controls();status('Processando…');
    try{await fn();}catch(error){status(error.message||String(error),true);}finally{busy=false;controls();}
  }
  function selectAudio(value){
    if(value.size>1400000||!value.size)throw Error('Áudio vazio ou excedeu 1,4 MB');
    blob=value;if(url)URL.revokeObjectURL(url);url=URL.createObjectURL(blob);$('player').src=url;
    $('capture').textContent=`Áudio pronto · ${(value.size/1024).toFixed(1)} KiB. Ouça antes de enviar.`;
  }
  async function refresh(){
    inventory=await panel.call('stt_models',{variant:$('variant').value});
    if(disposed)return;
    const active=inventory.active;
    const activeRss=typeof active?.process_rss_mb==='number'?active.process_rss_mb.toFixed(0)+' MiB':'N/D';
    $('active').textContent=active?`Assist ativo: ${names[active.model]||active.model} · ${active.model==='fastconformer'?active.variant:'Português — fixo'} · ${active.backend} · CPU ${active.compute} · ${active.threads} threads · ${activeRss} · última inferência ${panel.duration(active.recent?.inference_ms)}`:'STT de produção parado ou sem confirmação de prontidão.';
    $('inventory').replaceChildren();
    const rtLabels={runtime_ready:'Runtime OK',runtime_missing:'Runtime ausente',dependency_missing:'Dependência ausente',dependency_conflict:'Conflito de deps',timeout:'Timeout no runtime'};
    const wtLabels={model_not_installed:'Pesos não baixados',downloading:'Baixando',verifying:'Verificando',model_corrupted:'Pesos corrompidos',model_ready:'Pesos instalados'};
    const ldLabels={unloaded:'Não carregado',loading:'Carregando',model_ready:'Modelo carregado',model_load_failed:'Falha na carga',oom:'Sem memória (OOM)',timeout:'Timeout na carga'};
    for(const model of inventory.models){
      const line=document.createElement('p');
      const isDownloading=model.state==='downloading'||model.weights_status==='downloading';
      const isVerifying=model.state==='verifying'||model.weights_status==='verifying';
      const isActiveOp=isDownloading||isVerifying;
      let dlInfo='';
      if(isDownloading){
        const doneMb=((model.bytes_downloaded||0)/1e6).toFixed(1);
        const totalMb=((model.total_bytes||model.download_bytes||0)/1e6).toFixed(1);
        const pctStr=typeof model.percent==='number'?`${model.percent.toFixed(1)}%`:'iniciando';
        const speedStr=typeof model.speed_mbps==='number'&&model.speed_mbps>0?` · ${model.speed_mbps.toFixed(1)} MB/s`:'';
        dlInfo=` · ${model.phase||'Baixando'}: ${pctStr} (${doneMb}/${totalMb} MB)${speedStr}`;
      }else if(isVerifying){
        dlInfo=` · ${model.phase||'Verificando integridade (SHA-256)...'}`;
      }
      const rtState=rtLabels[model.runtime_status]||(model.runtime_available===false?'Runtime indisponível':'Runtime OK');
      const wtState=wtLabels[model.weights_status]||(model.installed?'Pesos instalados':'Pesos não baixados');
      const ldState=ldLabels[model.load_status]||(states[model.state]||model.state);
      const rtNote=model.runtime_available===false&&model.runtime_note?` · ${model.runtime_note}`:'';
      line.textContent=`${model.name}${model.runtime?' · '+model.runtime:''} · ${model.language_label||'Português — fixo'}${model.download_bytes?' · download '+(model.download_bytes/1000000).toFixed(0)+' MB':''} · [${rtState} | ${wtState} | ${ldState}]${dlInfo}${rtNote}${!isActiveOp&&model.error?' · '+model.error:''}`;
      if(isActiveOp){
        const bar=document.createElement('progress');bar.max=100;
        if(isVerifying)bar.value=100;
        else if(typeof model.percent==='number')bar.value=model.percent;
        line.append(' ',bar);
      }
      $('inventory').append(line);
    }
    syncInstallButton();
    try{
      const mp=$('memoryPolicy')?.value||'auto';
      const session=await panel.call('stt_session',{memory_policy:mp,memory_limit_mb:mp==='custom'?Number($('memoryLimit')?.value||4608):null});
      if(!disposed)renderSession(session);
    }catch(_){renderSession(null);}
    clearTimeout(poll);
    const activeDownload=inventory.models.find(model=>['downloading','verifying'].includes(model.state)||['downloading','verifying'].includes(model.weights_status));
    const dlBar=$('downloadProgress');
    if(activeDownload){
      if(dlBar){
        dlBar.hidden=false;
        if(activeDownload.state==='verifying'||activeDownload.weights_status==='verifying')dlBar.value=100;
        else if(typeof activeDownload.percent==='number')dlBar.value=activeDownload.percent;
        else dlBar.removeAttribute('value');
      }
      if(activeDownload.state==='verifying'||activeDownload.weights_status==='verifying'){
        status(`${activeDownload.name}: ${activeDownload.phase||'Verificando integridade (SHA-256)...'}`);
      }else{
        const pctStr=typeof activeDownload.percent==='number'?`${activeDownload.percent.toFixed(1)}%`:'conectando...';
        const doneMb=((activeDownload.bytes_downloaded||0)/1e6).toFixed(1);
        const totalMb=((activeDownload.total_bytes||activeDownload.download_bytes||0)/1e6).toFixed(1);
        const speedStr=typeof activeDownload.speed_mbps==='number'&&activeDownload.speed_mbps>0?` (${activeDownload.speed_mbps.toFixed(1)} MB/s)`:'';
        status(`Baixando ${activeDownload.name}: ${pctStr} · ${doneMb} / ${totalMb} MB${speedStr}`);
      }
      poll=setTimeout(()=>refresh().catch(e=>status(e.message,true)),1500);
    }else{
      if(dlBar){dlBar.hidden=true;dlBar.value=0;}
      status('Modelos consultados. Testes não alteram o Assist.');
    }
  }
  $('record').onclick=async()=>{
    if(busy||recording)return;
    try{
      if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw Error('Gravação indisponível. Use HTTPS e permita o microfone, ou envie um arquivo.');
      recording=true;controls();stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if(disposed||!recording||section.hidden){release();recording=false;controls();return;}
      const type=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(t=>MediaRecorder.isTypeSupported(t));
      recorder=new MediaRecorder(stream,type?{mimeType:type}:undefined);const parts=[];const started=performance.now();
      recorder.ondataavailable=e=>{if(e.data.size)parts.push(e.data);};
      recorder.onerror=()=>{release();recording=false;controls();status('Falha na gravação; tente novamente ou envie um arquivo.',true);};
      recorder.onstop=()=>{const mime=recorder.mimeType;release();recording=false;controls();if(disposed)return;try{selectAudio(new Blob(parts,{type:mime}));}catch(e){status(e.message,true);}};
      recorder.start();status('Gravando. O microfone será liberado ao parar.');
      ticker=setInterval(()=>$('capture').textContent=`Gravando · ${Math.min(12,(performance.now()-started)/1000).toFixed(1)} s / 12 s`,100);
      timer=setTimeout(()=>stop(),11800);
    }catch(error){release();recording=false;controls();status('Microfone: '+error.message,true);}
  };
  function stop(){if(recording)stoppedAt=performance.now();if(recorder?.state==='recording')recorder.stop();else{recording=false;controls();}release();}
  $('stop').onclick=stop;
  $('file').onchange=()=>{try{stoppedAt=null;if($('file').files[0])selectAudio($('file').files[0]);}catch(e){status(e.message,true);}};
  $('variant').onchange=()=>task(refresh);
  $('model').onchange=()=>syncInstallButton();
  $('refresh').onclick=()=>task(refresh);
  $('memoryPolicy').onchange=()=>syncMemoryControls();
  $('load').onclick=()=>task(async()=>{const res=await panel.call('stt_load',{...sessionPayload(),warmup:false});renderSession(res.session||res);status('Modelo carregado e mantido residente em RAM.');});
  $('warmup').onclick=()=>task(async()=>{const res=await panel.call('stt_warmup',sessionPayload());renderSession(res.session||res);status('Modelo aquecido na sessão residente.');});
  $('unload').onclick=()=>task(async()=>{const res=await panel.call('stt_unload',{});renderSession(res.session||res);status('Modelo descarregado e RAM liberada.');});
  $('install').onclick=()=>task(async()=>{await panel.call('stt_install',{stt_model:$('model').value,variant:$('variant').value});await refresh();});
  $('remove').onclick=()=>task(async()=>{await panel.call('stt_remove',{stt_model:$('model').value,variant:$('variant').value});await refresh();});
  function render(){
    $('results').replaceChildren();
    if(!rows.length){$('summary').textContent='Nenhum teste no histórico desta sessão.';return;}
    const list=[...(last?.results||[])];
    const sortBy=$('sort')?.value||'order';
    const metricVal=row=>{
      const st=row.inference_stats;
      if(sortBy==='wer')return row.quality?.evaluated?row.quality.wer_final:Infinity;
      if(sortBy==='cer')return row.quality?.evaluated?row.quality.cer_final:Infinity;
      if(sortBy==='p50')return Number.isFinite(st?.p50_ms)?st.p50_ms:(Number.isFinite(row.inference_ms)?row.inference_ms:Infinity);
      if(sortBy==='p95')return Number.isFinite(st?.p95_ms)?st.p95_ms:(Number.isFinite(row.inference_ms)?row.inference_ms:Infinity);
      if(sortBy==='ram')return Number.isFinite(row.process_rss_mb)?row.process_rss_mb:Infinity;
      return 0;
    };
    if(sortBy!=='order')list.sort((a,b)=>metricVal(a)-metricVal(b));
    const readyRows=list.filter(r=>r.state==='ready'&&Number.isFinite(r.inference_stats?.p50_ms??r.inference_ms));
    const fastestModel=readyRows.length>=2?readyRows.reduce((best,r)=>((r.inference_stats?.p50_ms??r.inference_ms)<(best.inference_stats?.p50_ms??best.inference_ms)?r:best),readyRows[0]).model:null;
    const evalRows=readyRows.filter(r=>r.quality?.evaluated);
    const accurateModel=evalRows.length>=2?evalRows.reduce((best,r)=>{
      if(r.quality.wer_final!==best.quality.wer_final)return r.quality.wer_final<best.quality.wer_final?r:best;
      if(r.quality.cer_final!==best.quality.cer_final)return r.quality.cer_final<best.quality.cer_final?r:best;
      return (r.inference_stats?.p50_ms??r.inference_ms)<(best.inference_stats?.p50_ms??best.inference_ms)?r:best;
    },evalRows[0]).model:null;
    const table=document.createElement('table'),head=document.createElement('tr');
    for(const name of ['Modelo','Modo / Sessão','RAW','Corrigida','Exact match','WER RAW / Final','CER RAW / Final','Edições S/I/D','Carga fria','Aquecimento','Inferência P50','P95','Min / Méd / Máx / DP','Total do teste (D/P/V/L)','Backend timings','RTF','CPU %','RAM (Ini / Pico / Fim)','Estado']){const th=document.createElement('th');th.textContent=name;head.append(th);}table.append(head);
    for(const row of list){
      const tr=document.createElement('tr'),q=row.quality,st=row.inference_stats;
      const tags=[];
      if(fastestModel&&row.model===fastestModel)tags.push('Mais rápido');
      if(accurateModel&&row.model===accurateModel)tags.push('Mais preciso');
      const modelLabel=`${names[row.model]||row.model}${row.backend?' ('+row.backend+')':''}${tags.length?' ['+tags.join(', ')+']':''}`;
      const p50=st?.p50_ms??row.inference_ms;
      const p95Text=st?.repetitions>=2?panel.duration(st.p95_ms):'—';
      const statsText=st?`${panel.duration(st.min_ms)} / ${panel.duration(st.mean_ms)} / ${panel.duration(st.max_ms)} / ${panel.duration(st.stdev_ms)}`:'—';
      const breakdownText=`${panel.duration(row.total_ms??row.request_total_ms)} (${panel.duration(row.audio_decode_ms)} / ${panel.duration(row.preprocess_ms)} / ${panel.duration(row.vad_ms)} / ${panel.duration(row.lexical_ms)})`;
      const bt=row.backend_timings;
      const btText=bt?`wav ${panel.duration(bt.wav_encode_ms)} · http ${panel.duration(bt.http_roundtrip_ms)} (${bt.instrumentation_scope||'client'})`:'—';
      const sessTag=row.session_reused?'Residente (RAM)':'Carga fria';
      const modeText=`${row.inference_mode||'offline'} · ${row.execution_mode||'warm'} · ${sessTag}`;
      const ramIni=typeof row.ram_initial_mb==='number'?row.ram_initial_mb.toFixed(0):'—';
      const ramPeak=typeof row.ram_peak_mb==='number'?row.ram_peak_mb.toFixed(0):(typeof row.process_rss_mb==='number'?row.process_rss_mb.toFixed(0):'—');
      const ramFin=typeof row.ram_final_mb==='number'?row.ram_final_mb.toFixed(0):'—';
      const ramText=`${ramIni} / ${ramPeak} / ${ramFin} MiB`;
      const cpuText=Number.isFinite(row.cpu_percent)?`${row.cpu_percent.toFixed(1)}%`:'—';
      const werText=q?.evaluated?`${q.wer_raw.toFixed(3)} / ${q.wer_final.toFixed(3)}`:(q?.precision_status||'Precisão não mensurável');
      const cerText=q?.evaluated?`${q.cer_raw.toFixed(3)} / ${q.cer_final.toFixed(3)}`:(q?.precision_status||'Precisão não mensurável');
      const editsText=q?.evaluated?`${q.substitutions??0}S / ${q.insertions??0}I / ${q.deletions??0}D`:'—';
      const exactText=q?.evaluated?(q.exact_match_final?'Sim':'Não'):'Não avaliado';
      const stateLabel=row.error||(row.streaming_note?`${states[row.state]||row.state} (${row.streaming_note})`:(states[row.state]||row.state));
      const values=[modelLabel,modeText,row.raw_transcript??'—',row.final_transcript??'—',exactText,werText,cerText,editsText,
        panel.duration(row.cold_load_ms??row.model_load_ms),panel.duration(row.warmup_ms),panel.duration(p50),p95Text,statsText,breakdownText,btText,
        Number.isFinite(row.realtime_factor)?row.realtime_factor.toFixed(3):'—',cpuText,ramText,
        stateLabel];
      for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}table.append(tr);
    }
    $('results').append(table);
    const details=document.createElement('details'),label=document.createElement('summary'),output=document.createElement('pre');label.textContent='Métricas completas, parâmetros e qualidade';output.textContent=JSON.stringify(last,null,2);details.append(label,output);$('results').append(details);
    $('summary').textContent=Object.entries(names).map(([id,name])=>{
      const samples=rows.filter(row=>row.model===id&&Number.isFinite(row.inference_ms)).map(row=>row.inference_ms).sort((a,b)=>a-b);
      if(!samples.length)return null;
      const n=samples.length,median=n%2?samples[(n-1)/2]:(samples[n/2-1]+samples[n/2])/2;
      return `${name}: n=${n}, mediana ${panel.duration(median)}, P95 ${n>=20?panel.duration(samples[Math.ceil(n*.95)-1]):'amostra insuficiente (mínimo 20)'}`;
    }).filter(Boolean).join(' · ');
  }
  async function transcribe(compare){
    if(!blob)throw Error('Grave ou envie um áudio primeiro');
    const bytes=new Uint8Array(await blob.arrayBuffer());let encoded='';for(let i=0;i<bytes.length;i+=8192)encoded+=String.fromCharCode(...bytes.subarray(i,i+8192));
    const models=[...$('models').querySelectorAll('input:checked')].map(input=>input.value);
    const repetitions=Number($('repetitions')?.value||1);
    const preset=$('preset').value;
    const audioProcessing=preset==='custom'?{
      vad_enabled:$('vadEnabled').checked,
      hpf_enabled:$('hpfEnabled').checked,
      ns_enabled:$('nsEnabled').checked,
      ns_level:Number($('nsLevel').value),
      agc_enabled:$('agcEnabled').checked,
      vad_threshold:Number($('vadThreshold').value),
      vad_pre_roll_ms:Number($('vadPreRoll').value),
      vad_min_silence_ms:Number($('vadMinSilence').value)
    }:undefined;
    const mp=$('memoryPolicy')?.value||'auto';
    const retVal=$('retention')?.value||'900';
    const started=performance.now();last=await panel.call(compare?'stt_compare':'stt_test',{audio:btoa(encoded),stt_model:$('model').value,
      variant:$('variant').value,models,threads:Number($('threads').value),repetitions,warmup:repetitions>1||$('executionMode')?.value==='warmup_benchmark',profile:$('profile').value||null,
      execution_mode:$('executionMode')?.value||'warm',resident:$('resident')?.checked!==false,retention_seconds:retVal==='permanent'?null:Number(retVal),
      memory_policy:mp,memory_limit_mb:mp==='custom'?Number($('memoryLimit')?.value||4608):null,
      preset,audio_processing:audioProcessing,nemotron_profile:$('nemotronProfile').value,nemotron_streaming_profile:$('nemotronProfile').value,
      reference:$('reference').value,entity_reference:$('entityReference').value});
    if(last.processed_wav_b64){
      $('processedPlayer').src='data:audio/wav;base64,'+last.processed_wav_b64;
      const ap=last.audio_processing||{};
      $('processedMeta').textContent=`Áudio pós-processamento (${ap.preset||preset}) · VAD: ${ap.vad_ms??'—'} ms · AP: ${ap.audio_processing_ms??'—'} ms`;
    }else{$('processedPlayer').removeAttribute('src');$('processedMeta').textContent='';}
    last.browser_request_ms=performance.now()-started;
    last.stop_to_transcript_ms=stoppedAt===null?null:performance.now()-stoppedAt;
    last.browser_wait_before_send_ms=stoppedAt===null?null:started-stoppedAt;
    last.transport_and_orchestration_ms=Math.max(0,last.browser_request_ms-last.request_total_ms);
    last.reference=$('reference').value;for(const row of last.results||[])Object.assign(row,{reference:last.reference,entity_reference:$('entityReference').value,browser_request_ms:last.browser_request_ms,stop_to_transcript_ms:last.stop_to_transcript_ms,browser_wait_before_send_ms:last.browser_wait_before_send_ms,transport_and_orchestration_ms:last.transport_and_orchestration_ms});rows.push(...(last.results||[]));rows=rows.slice(-200);render();
    if(last?.session)renderSession(last.session);
    status(last.cancelled?'Teste cancelado pelo usuário. O Assist mantém o modelo de produção.':'Teste concluído. O Assist mantém o modelo de produção.');
  }
  $('sort').onchange=()=>render();
  $('cancel').onclick=async()=>{$('cancel').disabled=true;status('Cancelando benchmark STT…');try{await panel.call('stt_cancel',{});}catch(e){status(e.message||String(e),true);}};
  $('test').onclick=()=>task(()=>transcribe(false));$('compare').onclick=()=>task(()=>transcribe(true));
  $('apply').onclick=()=>task(async()=>{
    await refresh();const model=$('model').value,variant=$('variant').value;
    if(!inventory.models.find(row=>row.id===model)?.installed)throw Error('Instale o modelo antes de aplicar');
    if(!confirm(`Trocar o STT do Assist de ${inventory.active?names[inventory.active.model]:'parado'} para ${names[model]} (${variant})? Somente o STT será reiniciado.`)){status('Troca cancelada.');return;}
    await panel.call('stt_apply',{stt_model:model,variant,confirm:true});await refresh();status('Modelo aplicado e capacidade Wyoming confirmada.');
  });
  function download(format){
    if(!rows.length){status('Execute um teste antes de exportar.',true);return;}
    const safe=value=>{let text=String(value??'');if(/^[=+@-]/.test(text.trimStart())||/^[\t\r\n]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
    const keys=['model','variant','revision','backend','inference_mode','execution_mode','session_reused','streaming_note','language','device','compute','threads','repetitions','profile','state','error_code','error','entity_reference','raw_transcript','final_transcript','cold_load_ms','model_load_ms','warmup_ms','inference_ms','p50_ms','p95_ms','min_ms','mean_ms','max_ms','stdev_ms','lexical_ms','preprocess_ms','vad_ms','audio_duration_ms','effective_audio_duration_ms','audio_decode_ms','queue_ms','total_ms','request_total_ms','realtime_factor','process_rss_mb','ram_initial_mb','ram_peak_mb','ram_final_mb','cpu_time_ms','cpu_percent','audio_sha256','reference','browser_request_ms','stop_to_transcript_ms','browser_wait_before_send_ms','transport_and_orchestration_ms','exact_match_raw','exact_match_final','wer_raw','wer_final','cer_raw','cer_final','substitutions','insertions','deletions','action_correct_raw','action_correct_final','negation_preserved_raw','negation_preserved_final','entity_correct_raw','entity_correct_final'];
    const payload={exported_at:new Date().toISOString(),language:'pt',language_fixed:true,last,history:rows};
    const data=format==='json'?JSON.stringify(payload,null,2):[keys.join(','),...rows.map(row=>keys.map(key=>safe(row[key]??row.inference_stats?.[key]??row.quality?.[key])).join(','))].join('\r\n');
    const href=URL.createObjectURL(new Blob([data],{type:format==='json'?'application/json':'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=href;link.download='arandu-stt.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
  }
  $('json').onclick=()=>download('json');$('csv').onclick=()=>download('csv');
  $('clear').onclick=()=>{rows=[];last=null;render();status('Histórico de testes removido desta sessão.');};
  const dispose=()=>{disposed=true;clearTimeout(poll);stop();if(url)URL.revokeObjectURL(url);blob=null;rows=[];};
  panel.sttDispose=dispose;
  panel.sttControls=controls;
  panel.sttStop=()=>{if(recording)stop();};
  task(refresh);
}
