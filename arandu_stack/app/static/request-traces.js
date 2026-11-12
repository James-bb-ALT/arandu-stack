/* Request telemetry only. Opening this view never runs a model or HA action. */
'use strict';
(() => {
  const section=$('flow'), topology=el('details',undefined,'trace-topology');
  topology.append(el('summary','Como funciona o fluxo de inteligência'));
  topology.append(...section.children);section.replaceChildren();
  const view=el('div');
  view.innerHTML=`<div class="section-head"><div><h2>Por onde passou o seu pedido?</h2><p>Requisições reais recebidas pela integração Arandu no Home Assistant.</p></div><button id="tracesRefresh" class="secondary">Atualizar requisições</button></div>
    <div class="trace-toolbar"><label for="traceSelect">Requisição<select id="traceSelect"><option value="">Nenhuma requisição registrada</option></select></label><button id="traceLatest" class="secondary">Mais recente</button><label><input id="traceAuto" type="checkbox" checked> Atualizar a cada 2 segundos</label></div>
    <p id="tracesStatus" role="status" aria-live="polite">Abra esta seção para consultar as requisições.</p>
    <p id="traceWindow" role="status"></p><p id="traceQueues" role="status"></p><button id="traceMetricsCsv" class="secondary">Exportar métricas CSV</button>
    <div id="traceSummary" class="policy-note" hidden></div><div id="traceStages" class="trace-stages"></div>
    <details class="policy-note"><summary>Dados da requisição</summary><pre id="traceJson" class="log-output"></pre><button id="traceExport" class="secondary" disabled data-unavailable="true">Exportar esta requisição</button></details>
    <div class="trace-settings"><label><input id="traceText" type="checkbox"> Incluir texto reconhecido nas próximas requisições</label><button id="tracesClear" class="secondary">Limpar histórico</button></div>
    <p class="footnote">Últimas 50 requisições em memória; reiniciar o app apaga o histórico e desliga a coleta de texto. Desmarcar a coleta também remove os textos já guardados. Não são armazenados áudio, tokens ou URLs TTS. Wake, STT e TTS aparecem como não observados: este rastreamento mede o trecho de interpretação e execução no HA, não a reprodução física no satélite.</p>`;
  section.append(view,topology);
  let active=false,timer=null,busy=false,follow=true,latest=null;
  const labels={running:'Em execução',pending:'Aguardando',not_observed:'Não observado',not_reached:'Não necessário / não alcançado',disabled:'Desabilitado',completed:'Concluído',accepted:'Aceito',rejected:'Rejeitado',error:'Erro técnico',confirmation_required:'Aguardando confirmação',clarification_required:'Aguardando esclarecimento',cancelled:'Cancelado',uncalibrated:'Sem calibração',ambiguous:'Ambíguo'};
  const names={wake:'Wake',stt:'STT',prepare:'Preparação no app',normalization:'Normalização',authorization:'Identidade inicial',queue:'Fila de admissão / sessão',nlu_queue:'Fila NLU',model_queue:'Fila de modelo',resource_queue:'Fila de recursos HA',nlu:'NLU',decision:'Decision',llm:'LLM',validation:'Validação HA',ha:'Execução / consulta HA',tts:'TTS',playback:'Reprodução no satélite',scheduler:'Agendamento independente'};
  const reasons={joint_calibration_missing:'Sem calibração válida',below_threshold:'Confiança abaixo do mínimo',insufficient_margin:'Margem insuficiente',no_match:'Nenhuma interpretação',target_not_found:'Alvo não encontrado',no_compatible_capability:'Nenhum alvo com a capacidade pedida',permission_denied:'Permissão negada',backend_unavailable:'Motor inacessível',semantic_proposal_rejected:'Proposta recusada pela validação',TimeoutError:'Tempo limite excedido',ConnectionError:'Falha de conexão'};
  const duration=value=>Number.isFinite(value)?value>=1000?(value/1000).toFixed(2).replace('.',',')+' s':value.toFixed(1).replace('.',',')+' ms':'Não medido';
  const time=value=>new Date(value).toLocaleTimeString();
  function selected(){return latest?.requests.find(row=>row.id===$('traceSelect').value);}
  function render(){
    const trace=selected();$('traceSummary').hidden=!trace;$('traceStages').replaceChildren();$('traceJson').textContent=trace?JSON.stringify(trace,null,2):'';$('traceExport').disabled=!trace;
    if(trace)delete $('traceExport').dataset.unavailable;else $('traceExport').dataset.unavailable='true';
    if(!trace)return;
    const summary=$('traceSummary');summary.replaceChildren();
    const stale=trace.status==='running'&&Date.now()-Date.parse(trace.received_at)>120000;
    summary.append(el('strong',stale?'Sem atualização há mais de 2 minutos; estado final desconhecido':labels[trace.status]||trace.status),el('p',trace.text||'Texto não coletado. Você pode habilitar a coleta abaixo.'),
      el('p',time(trace.started_at)+' · '+({satellite:'Satélite',device:'Dispositivo',unknown:'Texto / origem não informada'}[trace.origin])+' · Total: '+duration(trace.total_ms)),
      el('small','ID '+trace.id+' · resultado: '+(trace.result||'em andamento')+' · última atualização: '+time(trace.received_at)));
    if(trace.reason)summary.append(el('p','Motivo: '+(reasons[trace.reason]||trace.reason)));
    if(trace.job_ref)summary.append(el('p','Agendamento '+trace.job_kind+' · referência '+trace.job_ref+' · vencimento '+new Date(trace.scheduled_at).toLocaleString()));
    for(const [name,title] of Object.entries(names)){
      const row=trace.stages[name]||{status:'not_observed'},card=el('article',undefined,'trace-stage');card.dataset.status=row.status;
      const heading=el('div',undefined,'trace-stage-head');
      if(['wake','stt','nlu','decision','llm','tts'].includes(name)){const icon=el('img');icon.src='static/assets/brand/'+name+'.svg';icon.alt='';icon.width=32;icon.height=32;heading.append(icon);}
      heading.append(el('h3',title),el('span',labels[row.status]||row.status,'status'));card.append(heading);
      card.append(el('p',row.duration_ms==null?'Tempo não medido':'Duração: '+duration(row.duration_ms)));
      if(row.offset_ms!=null)card.append(el('small','Início: +'+duration(row.offset_ms)+(row.attempts>1?' · '+row.attempts+' tentativas':'')));
      if(row.reason)card.append(el('p',reasons[row.reason]||row.reason));
      if(row.confidence!=null)card.append(el('p','Confiança calibrada: '+(row.confidence*100).toFixed(1).replace('.',',')+'%'));
      if(row.model)card.append(el('p','Modelo: '+row.model+(row.backend?' · '+row.backend:'')+(row.embedding_used===false?' · sem embeddings':'')));
      if(row.load_ms!=null)card.append(el('p','Carga: '+duration(row.load_ms)+' · inferência: '+duration(row.inference_ms)));
      $('traceStages').append(card);
    }
  }
  async function refresh(){
    clearTimeout(timer);if(!active||document.hidden||busy)return;
    busy=true;
    try{
      const data=await api('traces'),old=$('traceSelect').value;latest=data;
      const metrics=data.summary;
      $('traceWindow').textContent=metrics?`Janela de ${metrics.window_seconds}s, até ${metrics.retained_limit} pedidos · ${metrics.completed} concluídos · p50 ${duration(metrics.integration_ms.p50)} · p95 ${duration(metrics.integration_ms.p95)} · p99 ${duration(metrics.integration_ms.p99)} · erros ${metrics.errors} · timeouts ${metrics.timeouts} · saturados ${metrics.saturated}. Áudio a áudio: não observado.`:'';
      $('traceQueues').textContent=data.queues?Object.entries(data.queues).map(([key,row])=>`${names[key]||key}: ${row.active} em execução · ${row.waiting} em espera · ${row.rejected} recusados`).join(' | '):'Filas internas não disponíveis.';
      $('traceSelect').replaceChildren();
      for(const row of data.requests){const option=el('option',time(row.started_at)+' · '+(row.text||row.id.slice(0,8))+' · '+(labels[row.status]||row.status));option.value=row.id;$('traceSelect').append(option);}
      if(!follow&&data.requests.some(row=>row.id===old))$('traceSelect').value=old;
      $('traceText').checked=data.include_text;
      $('tracesStatus').textContent=data.requests.length?data.requests.length+' requisições · '+(follow?'seguindo a mais recente.':'seleção mantida.'):'Nenhuma requisição recebida. Use o Assist com Arandu e confira o endpoint do app na integração HACS atualizada. Histórico não é retroativo.';
      render();
    }catch(error){$('tracesStatus').textContent='Não foi possível atualizar: '+error.message;}
    finally{busy=false;if(active&&!document.hidden&&$('traceAuto').checked)timer=setTimeout(refresh,2000);}
  }
  $('tracesRefresh').onclick=refresh;
  $('traceMetricsCsv').onclick=()=>{
    if(!latest?.summary)return;
    const m=latest.summary,rows=[['stage','p50_ms','p95_ms','p99_ms','active','waiting','rejected'],...Object.entries({integration:m.integration_ms,...m.stages}).map(([stage,v])=>[stage,v.p50??'',v.p95??'',v.p99??'','','','']),...Object.entries(latest.queues||{}).map(([stage,v])=>['gate_'+stage,'','','',v.active,v.waiting,v.rejected])];
    const url=URL.createObjectURL(new Blob([rows.map(row=>row.join(',')).join('\n')],{type:'text/csv'})),link=el('a');link.href=url;link.download='arandu-metrics.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('traceSelect').onchange=()=>{follow=false;render();};
  $('traceLatest').onclick=()=>{follow=true;if(latest?.requests[0])$('traceSelect').value=latest.requests[0].id;render();};
  $('traceAuto').onchange=()=>{$('traceAuto').checked?refresh():clearTimeout(timer);};
  $('traceText').onchange=()=>task(async()=>{await api('traces/options',{include_text:$('traceText').checked});await refresh();});
  $('tracesClear').onclick=()=>task(async()=>{await api('traces/clear',{});latest=null;await refresh();});
  $('traceExport').onclick=()=>{
    const trace=selected();if(!trace)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(trace,null,2)],{type:'application/json'})),link=el('a');link.href=url;link.download='arandu-request-'+trace.id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(active&&!document.hidden&&$('traceAuto').checked)refresh();});
  window.AranduTraces={activate(tab){active=tab==='flow';clearTimeout(timer);if(active)refresh();}};
})();
