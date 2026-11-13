/* Native STT view. Audio and transcripts remain in memory until explicitly exported. */
export function mountSTT(panel) {
  const section=panel.$('sttLab');
  section.innerHTML=`<h2>STT / Reconhecimento de voz</h2>
    <p>Grave até 12 segundos ou envie WAV, WebM/Opus, Ogg ou MP4/AAC (até 1,4 MB). Nenhuma ação doméstica será executada.</p>
    <div class="row"><button data-stt="record">Gravar áudio</button><button data-stt="stop" disabled>Parar gravação</button>
    <label>Enviar arquivo WAV/áudio<input data-stt="file" type="file" accept="audio/wav,audio/webm,audio/ogg,audio/mp4,video/mp4"></label></div>
    <p data-stt="capture" role="status" aria-live="polite">Nenhum áudio selecionado.</p><audio data-stt="player" controls></audio>
    <div class="row"><label>Modelo selecionado<select data-stt="model"><option value="fastconformer">Arandu FastConformer</option><option value="whisper_tiny">Whisper Tiny</option><option value="whisper_base">Whisper Base</option><option value="whisper_small">Whisper Small</option><option value="parakeet_ptbr_30s">Parakeet TDT V3 pt-BR 30s</option><option value="parakeet_tagarela">Parakeet TAGARELA pt-BR</option><option value="orukeet_int8" selected>Orukeet INT8</option><option value="granite_turboctc_q4">Granite TurboCTC Q4_K_M</option><option value="parakeet_ultra_q8">Parakeet Ultra Q8_0</option><option value="parakeet_ultra_q4">Parakeet Ultra Q4_K_M</option><option value="phonon_2_5">Phonon 2.5</option><option value="nemotron_unified_0_6b">Nemotron Unified ASR 0.6B</option></select></label>
    <label>Variante FastConformer<select data-stt="variant"><option value="adapted">Adaptado</option><option value="original">Original</option></select></label>
    <label>Threads<select data-stt="threads"><option>1</option><option selected>2</option><option>3</option><option>4</option></select></label>
    <label>Correção neste teste<select data-stt="profile"><option value="">Padrão do motor</option><option value="raw">RAW</option><option value="normalize_only">Somente normalização</option><option value="full">Completa</option></select></label>
    <label>Perfil Nemotron<select data-stt="nemotronProfile"><option value="offline">Offline</option><option value="precision">Precisão (560/560ms)</option><option value="balanced">Balanceado (160/400ms)</option><option value="fast">Rápido (80/160ms)</option><option value="experimental">Experimental (80/80ms)</option></select></label></div>
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
    <button class="primary" data-stt="test">Transcrever com modelo selecionado</button><button data-stt="compare">Comparar modelos</button></div>
    <p data-stt="status" role="status" aria-live="polite">Configure o endpoint Arandu Stack na integração para conectar.</p>
    <p data-stt="active"></p><div data-stt="inventory"></div><div data-stt="results" class="scroll"></div>
    <p data-stt="summary"></p><div class="row"><button data-stt="apply">Usar este modelo no Assist</button><button data-stt="json">Exportar JSON</button>
    <button data-stt="csv">Exportar CSV</button><button data-stt="clear">Limpar histórico de testes</button></div>`;
  const $=id=>section.querySelector(`[data-stt="${id}"]`);
  let blob=null,url=null,stream=null,recorder=null,timer=null,ticker=null,disposed=false,busy=false,recording=false,stoppedAt=null,rows=[],last=null,inventory=null,poll=null;
  const names={fastconformer:'Arandu FastConformer',whisper_tiny:'Whisper Tiny',whisper_base:'Whisper Base',whisper_small:'Whisper Small',parakeet_ptbr_30s:'Parakeet TDT V3 pt-BR 30s',parakeet_tagarela:'Parakeet TAGARELA pt-BR',orukeet_int8:'Orukeet INT8',granite_turboctc_q4:'Granite TurboCTC Q4_K_M',parakeet_ultra_q8:'Parakeet Ultra Q8_0',parakeet_ultra_q4:'Parakeet Ultra Q4_K_M',phonon_2_5:'Phonon 2.5',nemotron_unified_0_6b:'Nemotron Unified ASR 0.6B'};
  const states={not_installed:'Não instalado',downloading:'Baixando',verifying:'Verificando',installed:'Instalado',loading:'Carregando',ready:'Pronto',error:'Erro'};
  for(const [id,name] of Object.entries(names)){
    const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=id;input.checked=['fastconformer','whisper_base'].includes(id);
    label.append(input,document.createTextNode(' '+name));$('models').append(label);
  }
  function status(text,error=false){$('status').textContent=text;$('status').setAttribute('role',error?'alert':'status');}
  function release(){clearTimeout(timer);clearInterval(ticker);stream?.getTracks().forEach(track=>track.stop());stream=null;}
  function controls(){
    section.querySelectorAll('button,input,select').forEach(node=>node.disabled=busy||recording);
    $('stop').disabled=!recording;
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
    $('active').textContent=active?`Assist ativo: ${names[active.model]||active.model} · ${active.model==='fastconformer'?active.variant:'multilíngue'} · ${active.backend} · CPU ${active.compute} · ${active.threads} threads · ${active.process_rss_mb.toFixed(0)} MiB · última inferência ${panel.duration(active.recent?.inference_ms)}`:'STT de produção parado ou sem confirmação de prontidão.';
    $('inventory').replaceChildren();
    for(const model of inventory.models){const line=document.createElement('p');line.textContent=`${model.name}${model.download_bytes?' · download '+(model.download_bytes/1000000).toFixed(0)+' MB':''}: ${states[model.state]||model.state}${model.error?' · '+model.error:''}`;$('inventory').append(line);}
    clearTimeout(poll);
    if(inventory.models.some(model=>['downloading','verifying'].includes(model.state))){status('Baixando pesos. Você pode acompanhar o estado aqui.');poll=setTimeout(()=>refresh().catch(e=>status(e.message,true)),2000);}
    else status('Modelos consultados. Testes não alteram o Assist.');
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
  $('refresh').onclick=()=>task(refresh);
  $('install').onclick=()=>task(async()=>{await panel.call('stt_install',{stt_model:$('model').value,variant:$('variant').value});await refresh();});
  $('remove').onclick=()=>task(async()=>{await panel.call('stt_remove',{stt_model:$('model').value,variant:$('variant').value});await refresh();});
  function render(){
    $('results').replaceChildren();
    if(!rows.length){$('summary').textContent='Nenhum teste no histórico desta sessão.';return;}
    const table=document.createElement('table'),head=document.createElement('tr');
    for(const name of ['Modelo','RAW','Corrigida','Inferência','Carregamento','Total do teste','Lexical','WER RAW / FINAL','CER RAW / FINAL','Estado']){const th=document.createElement('th');th.textContent=name;head.append(th);}table.append(head);
    for(const row of last?.results||[]){
      const tr=document.createElement('tr'),q=row.quality;
      const values=[names[row.model],row.raw_transcript??'—',row.final_transcript??'—',panel.duration(row.inference_ms),panel.duration(row.model_load_ms),panel.duration(row.request_total_ms),panel.duration(row.lexical_ms),
        q?.evaluated?`${q.wer_raw.toFixed(3)} / ${q.wer_final.toFixed(3)}`:'Não avaliado',q?.evaluated?`${q.cer_raw.toFixed(3)} / ${q.cer_final.toFixed(3)}`:'Não avaliado',row.error||states[row.state]];
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
    const started=performance.now();last=await panel.call(compare?'stt_compare':'stt_test',{audio:btoa(encoded),stt_model:$('model').value,
      variant:$('variant').value,models,threads:Number($('threads').value),profile:$('profile').value||null,
      preset,audio_processing:audioProcessing,nemotron_profile:$('nemotronProfile').value,
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
    last.reference=$('reference').value;for(const row of last.results)Object.assign(row,{reference:last.reference,entity_reference:$('entityReference').value,browser_request_ms:last.browser_request_ms,stop_to_transcript_ms:last.stop_to_transcript_ms,browser_wait_before_send_ms:last.browser_wait_before_send_ms,transport_and_orchestration_ms:last.transport_and_orchestration_ms});rows.push(...last.results);rows=rows.slice(-200);render();
    status('Teste concluído. O Assist mantém o modelo de produção.');
  }
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
    const keys=['model','variant','revision','backend','device','compute','threads','profile','state','error','entity_reference','raw_transcript','final_transcript','inference_ms','model_load_ms','lexical_ms','audio_duration_ms','audio_decode_ms','queue_ms','request_total_ms','realtime_factor','process_rss_mb','audio_sha256','reference','browser_request_ms','stop_to_transcript_ms','browser_wait_before_send_ms','transport_and_orchestration_ms','wer_raw','wer_final','cer_raw','cer_final','action_correct_raw','action_correct_final','negation_preserved_raw','negation_preserved_final','entity_correct_raw','entity_correct_final'];
    const data=format==='json'?JSON.stringify({last,history:rows},null,2):[keys.join(','),...rows.map(row=>keys.map(key=>safe(row[key]??row.quality?.[key])).join(','))].join('\r\n');
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
