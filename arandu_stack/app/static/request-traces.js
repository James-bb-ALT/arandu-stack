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
    <div id="traceSummary" class="policy-note" hidden></div><div id="traceStages" class="trace-stages"></div>
    <details class="policy-note"><summary>Dados da requisição</summary><pre id="traceJson" class="log-output"></pre><button id="traceExport" class="secondary" disabled data-unavailable="true">Exportar esta requisição</button></details>
    <div class="trace-settings"><label><input id="traceText" type="checkbox"> Incluir texto reconhecido nas próximas requisições</label><button id="tracesClear" class="secondary">Limpar histórico</button></div>
    <p class="footnote">Últimas 50 requisições em memória; reiniciar o app apaga o histórico e desliga a coleta de texto. Desmarcar a coleta também remove os textos já guardados. Não são armazenados áudio, tokens ou URLs TTS. Wake, STT e TTS aparecem como não observados: este rastreamento mede o trecho de interpretação e execução no HA, não a reprodução física no satélite.</p>`;
  section.append(view,topology);
  let active=false,timer=null,busy=false,follow=true,latest=null;
  const labels={running:'Em execução',pending:'Aguardando',not_observed:'Não observado',not_reached:'Não necessário / não alcançado',disabled:'Desabilitado',completed:'Concluído',accepted:'Aceito',rejected:'Rejeitado',error:'Erro técnico',confirmation_required:'Aguardando confirmação',clarification_required:'Aguardando esclarecimento',cancelled:'Cancelado',uncalibrated:'Sem calibração',ambiguous:'Ambíguo'};
  const names={wake:'Wake',stt:'STT',prepare:'Preparação no app',authorization:'Identidade inicial',queue:'Espera na fila',nlu:'NLU',decision:'Decision',llm:'LLM',validation:'Validação HA',ha:'Execução / consulta HA',tts:'TTS'};
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
    for(const [name,title] of Object.entries(names)){
      const row=trace.stages[name]||{status:'not_observed'},card=el('article',undefined,'trace-stage');card.dataset.status=row.status;
      const heading=el('div',undefined,'trace-stage-head');
      if(['wake','stt','nlu','decision','llm','tts'].includes(name)){const icon=el('img');icon.src='static/assets/brand/'+name+'.svg';icon.alt='';icon.width=32;icon.height=32;heading.append(icon);}
      heading.append(el('h3',title),el('span',labels[row.status]||row.status,'status'));card.append(heading);
      card.append(el('p',row.duration_ms==null?'Tempo não medido':'Duração: '+duration(row.duration_ms)));
      if(row.offset_ms!=null)card.append(el('small','Início: +'+duration(row.offset_ms)+(row.attempts>1?' · '+row.attempts+' tentativas':'')));
      if(row.reason)card.append(el('p',reasons[row.reason]||row.reason));
      if(row.confidence!=null)card.append(el('p','Confiança calibrada: '+(row.confidence*100).toFixed(1).replace('.',',')+'%'));
      $('traceStages').append(card);
    }
  }
  async function refresh(){
    clearTimeout(timer);if(!active||document.hidden||busy)return;
    busy=true;
    try{
      const data=await api('traces'),old=$('traceSelect').value;latest=data;
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
