'use strict';
let state=null, editing=null, dirty=false;
let activeTab='overview',logTimer=null,logsBusy=false,logsPending=false,latestLogs=null,logsSignature='';
const $=id=>document.getElementById(id);
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function api(path,body){
  const response=await fetch('api/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Arandu-CSRF':state?.csrf||''},body:body===undefined?undefined:JSON.stringify(body)});
  let data;try{data=await response.json();}catch(e){throw Error(response.status===403?'Acesso recusado. Atualize a página e confira as permissões do app.':'O serviço não retornou uma resposta válida.');}
  if(!response.ok)throw Error(data.error||data.message||'Não foi possível concluir.');return data;
}
async function task(fn){
  document.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{await fn();}catch(e){notice(e.message,true);}
  finally{document.querySelectorAll('button').forEach(b=>b.disabled=false);document.querySelectorAll('[data-unavailable]').forEach(b=>b.disabled=true);}
}
async function refresh(){
  state=await api('state');renderModules();renderOverview();if(!dirty)renderPhrases();
  $('appVersion').textContent='Arandu Assistant · '+state.version;
  notice(state.local?'Prévia local · os motores Linux estão incluídos no app Arandu Assistant.':'Estado atualizado. Todos os módulos pertencem ao mesmo app.');
}
function renderModules(){
  $('cards').replaceChildren();
  state.modules.forEach((module,index)=>{
    const card=el('article',undefined,'card'),top=el('div',undefined,'card-top');
    const icon=el('img',undefined,'engine-icon');icon.src='static/assets/brand/'+module.id+'.svg';icon.alt='';icon.width=38;icon.height=38;top.append(icon);
    const on=module.state==='started';
    top.append(el('span',module.state==='error'?'Falha':on?'Ativo':module.installed?'Desativado':'Runtime indisponível','status'+(on?' on':module.state==='error'?' error':'')));
    card.append(top,el('h3',module.name),el('p',module.description));
    const actions=el('div',undefined,'card-actions');
    const toggle=el('button',on?'Desativar':'Ativar',on?'secondary':'');
    toggle.onclick=()=>task(async()=>{notice('Aplicando alteração em '+module.name+'…');await api('modules/'+module.id+'/'+(on?'stop':'start'),{});await refresh();});
    const config=el('button','Configurar','secondary');config.onclick=()=>openSettings(module);
    const restart=el('button','Reiniciar','secondary');
    restart.onclick=()=>task(async()=>{if(module.id==='stt'){if(!confirm('Reiniciar o STT e verificar o modelo '+module.options.stt_model+'?'))return;await api('stt/apply',{model:module.options.stt_model,variant:module.options.model_variant,confirm:true});}else await api('modules/'+module.id+'/restart',{});await refresh();});
    [toggle,config,restart].forEach(b=>{if(!module.installed){b.disabled=true;b.dataset.unavailable='true';}actions.append(b);});
    const logs=el('button',module.state==='error'?'Ver erro':'Logs','secondary');
    logs.onclick=()=>{ $('logsSource').value=module.id; selectTab('logs'); };
    actions.append(logs);
    if(module.id==='stt'){const test=el('a','Testar STT','button secondary');test.href='/arandu-ai-lab#stt';test.target='_top';actions.append(test);}
    card.append(actions,el('div',module.slug||'Módulo incluído no Arandu Assistant.','slug'));$('cards').append(card);
    if(module.last_error)card.append(el('p',module.last_error,'log-error'));
    if(module.id==='stt'){const actual=el('p','Consultando motor realmente ativo…','slug');card.append(actual);api('stt/models').then(result=>{if(actual.isConnected)actual.textContent=result.active?'Assist: '+result.active.model+' · '+(result.active.model==='fastconformer'?result.active.variant:'multilíngue')+' · '+result.active.process_rss_mb.toFixed(0)+' MiB'+(Number.isFinite(result.active.recent?.inference_ms)?' · '+result.active.recent.inference_ms.toFixed(1)+' ms':''):'Modelo de produção ainda não confirmou prontidão.';}).catch(()=>{actual.textContent='Diagnóstico STT indisponível.';});}
  });
}
function renderOverview(){
  $('overviewCards').replaceChildren();
  const running=state.modules.filter(m=>m.state==='started').length,failed=state.modules.filter(m=>m.state==='error').length;
  $('processSummary').textContent=running+' de '+state.modules.length+' processos ativos'+(failed?' · '+failed+' com falha':'')+'. Prontidão dos modelos não verificada aqui.';
  for(const [index,source] of [...$('cards').children].entries()){
    const card=source.cloneNode(true);card.querySelector('.slug')?.remove();card.querySelector('.log-error')?.remove();
    const action=el('button','Gerenciar módulo','secondary');action.onclick=()=>{selectTab('modules');$('cards').children[index]?.scrollIntoView({block:'nearest'});};
    card.querySelector('.card-actions').replaceChildren(action);$('overviewCards').append(card);
  }
}
function openSettings(module){
  editing=module;$('configTitle').textContent=module.name;$('configError').textContent='';$('configFields').replaceChildren();
  const sections=new Map();
  for(const field of module.schema||[]){
    let group=sections.get(field.section);
    if(!group){group=el('fieldset',undefined,'setting-section');group.append(el('legend',field.section));sections.set(field.section,group);$('configFields').append(group);}
    const row=el('div',undefined,'setting-field');row.dataset.field=field.id;
    const id='setting-'+module.id+'-'+field.id;
    const label=el('label',field.label);label.htmlFor=id;row.append(label);
    const current=module.options[field.id];
    let input;
    if(field.type==='select'){
      input=el('select');
      for(const [value,text]of field.choices||[]){const option=el('option',text);option.value=String(value);option.selected=String(value)===String(current);input.append(option);}
    }else{
      input=el('input');
      input.type=field.type==='switch'?'checkbox':field.type;
      if(field.type==='switch'){input.checked=!!current;input.setAttribute('role','switch');}
      else if(field.type==='password'){input.autocomplete='new-password';input.placeholder=field.configured?'Token já salvo · deixe vazio para manter':'Informe o token';}
      else{input.value=current??'';}
      if(field.type==='number'){input.min=field.min;input.max=field.max;input.step=field.step;input.required=true;}
    }
    input.id=id;input.name=field.id;input.dataset.setting=field.id;input.className='setting-input';
    if(field.type==='select')input.required=true;
    row.classList.toggle('setting-switch',field.type==='switch');row.append(input);
    if(field.help){const help=el('small',field.help,'setting-help');help.id=id+'-help';input.setAttribute('aria-describedby',help.id);row.append(help);}
    if(field.type==='password'&&field.configured){
      const clearLabel=el('label',undefined,'secret-clear'),clear=el('input');
      clear.type='checkbox';clear.dataset.clearFor=field.id;
      clearLabel.append(clear,el('span','Remover token salvo'));row.append(clearLabel);
      clear.onchange=()=>updateDependencies();
    }
    input.onchange=()=>updateDependencies();group.append(row);
  }
  const apply=$('saveRestart');apply.disabled=module.state!=='started'&&module.id!=='stt';apply.textContent=module.id==='stt'?'Salvar e aplicar no Assist':module.id==='decision'?'Salvar e aplicar':'Salvar e reiniciar';
  apply.title=apply.disabled?'Ative o módulo para aplicar imediatamente':'Salva as opções e reinicia somente este módulo';
  if(apply.disabled)apply.dataset.unavailable='true';else delete apply.dataset.unavailable;
  updateDependencies();$('configDialog').showModal();
  if(module.id==='stt')renderSttManager();
  if(module.id==='llm')renderLlmManager();
  if(module.id==='decision')renderDecisionManager();
}
function settingInput(name){return $('configFields').querySelector('[data-setting="'+name+'"]');}
function updateDependencies(){
  for(const field of editing?.schema||[]){
    const row=$('configFields').querySelector('[data-field="'+field.id+'"]'),input=settingInput(field.id);
    const engine=editing?.id==='stt'?settingInput('stt_model')?.value:null;
    const active=(!field.depends_on||!!settingInput(field.depends_on)?.checked) && !(engine&&['model_variant','profile','closed_vocabulary'].includes(field.id)&&engine!=='fastconformer') && !(engine&&field.id==='whisper_profile'&&engine==='fastconformer');
    row.hidden=!active;input.disabled=!active;
    const clear=row.querySelector('[data-clear-for]');
    if(clear){clear.disabled=!active;if(clear.checked)input.disabled=true;}
    if(field.id==='voice')for(const option of input.options)option.disabled=option.value.startsWith('pocket-')&&!settingInput('pocket_enabled')?.checked;
  }
  for(const section of $('configFields').querySelectorAll('fieldset'))section.hidden=![...section.querySelectorAll('.setting-field')].some(row=>!row.hidden);
}
function settingsPayload(){
  const values={};
  for(const field of editing.schema||[]){
    const row=$('configFields').querySelector('[data-field="'+field.id+'"]'),input=settingInput(field.id);
    if(row.hidden)continue;
    if(field.type==='password'){
      if(row.querySelector('[data-clear-for]')?.checked)values[field.id]='';
      else if(input.value)values[field.id]=input.value;
      continue;
    }
    if(input.disabled)continue;
    if(field.type==='switch')values[field.id]=input.checked;
    else if(field.value_type==='int'||field.value_type==='float')values[field.id]=Number(input.value);
    else values[field.id]=input.value;
  }
  return values;
}
async function saveSettings(restart){
  if(!$('configForm').reportValidity())return;
  $('configError').textContent='';
  let decisionApplied=false;
  try{
    const options=settingsPayload();
    if(editing.id==='decision'&&(restart||options.model!==editing.options.model||options.backend!==editing.options.backend)){
      if(!confirm('Verificar e aplicar '+options.model+' em '+options.backend+' no Decision?'))return;
      await api('decision/apply',{model:options.model,backend:options.backend,options,confirm:true});
      decisionApplied=true;
    }else if(editing.id==='stt'&&restart){
      if(!confirm('Aplicar '+options.stt_model+' no Assist? Somente o STT será reiniciado.'))return;
      await api('stt/apply',{model:options.stt_model,variant:options.model_variant||editing.options.model_variant,options,confirm:true});
      }else if(editing.id==='llm'&&restart){
        if(!confirm('Aplicar '+options.llm_model+' no Assist após verificar a carga?'))return;
        await api('llm/apply',{model:options.llm_model,options,confirm:true});
    }else{
      await api('modules/'+editing.id+'/options',{options});
      if(restart&&editing.state==='started')await api('modules/'+editing.id+'/restart',{});
    }
  }catch(error){$('configError').textContent=error.message;return;}
  $('configDialog').close();await refresh();
    notice(decisionApplied?'Decision aplicado; modelo, processador e inferência verificados.':restart?(editing.id==='llm'?'Configuração aplicada; carga e identidade do modelo verificadas.':'Configuração salva e módulo reiniciado.'):'Configuração salva. Reinicie o módulo para aplicar.');
}
function phraseRow(row){
  const item=el('div',undefined,'phrase-row'),enabled=el('input');
  enabled.type='checkbox';enabled.checked=row.enabled;enabled.setAttribute('aria-label','Atalho ativo');
  const phrase=el('input');phrase.type='text';phrase.value=row.phrase;phrase.placeholder='Sua frase';phrase.maxLength=512;phrase.setAttribute('aria-label','Frase personalizada');
  const command=el('input',undefined,'command');command.type='text';command.value=row.command;command.placeholder='Comando para o Arandu';command.maxLength=512;command.setAttribute('aria-label','Comando equivalente');
  [enabled,phrase,command].forEach(input=>input.oninput=()=>dirty=true);
  const remove=el('button','Remover','secondary');remove.onclick=()=>{item.remove();dirty=true;};
  item.append(enabled,phrase,el('span','→','arrow'),command,remove);return item;
}
function renderPhrases(){$('phraseRows').replaceChildren(...state.phrases.map(phraseRow));if(!state.phrases.length)$('phraseRows').append(phraseRow({phrase:'',command:'',enabled:true}));}
function selectTab(id){
  activeTab=id;clearTimeout(logTimer);
  document.querySelectorAll('nav [data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===id);if(b.dataset.tab===id)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  ['overview','modules','flow','lab','phrases','logs','help'].forEach(section=>$(section).hidden=section!==id);
  if(id==='logs')refreshLogs();
  window.AranduTraces?.activate(id);
}
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>selectTab(button.dataset.tab));

function logState(source){
  const labels={started:'Ativo',stopped:'Parado',error:'Falha'};
  return source.id==='app'?'Aplicativo':(labels[source.state]||'Estado indisponível')+(source.exit_code!==null&&source.exit_code!==undefined?' · saída '+source.exit_code:'');
}
function renderLogs(data){
  const positions=new Map([...$('logsContent').querySelectorAll('.log-source')].map(card=>{const pre=card.querySelector('pre');return [card.dataset.source,{top:pre.scrollTop,bottom:pre.scrollHeight-pre.clientHeight-pre.scrollTop<8}];}));
  $('logsContent').replaceChildren();
  for(const source of data.sources){
    const card=el('article',undefined,'log-source'),heading=el('div',undefined,'log-heading');
    card.dataset.source=source.id;
    heading.append(el('h3',source.name),el('span',logState(source),source.state==='error'?'log-error':'log-meta'));
    card.append(heading);
    if(source.modified)card.append(el('p','Última gravação: '+new Date(source.modified).toLocaleString(),'log-meta'));
    if(source.last_error)card.append(el('p',source.last_error,'log-error'));
    const text=source.error||source.text||(source.exists?'Nenhuma mensagem registrada até agora.':'Ainda não há arquivo de log desta fonte.');
    card.append(el('pre',text,'log-output'));
    if(source.truncated)card.append(el('p','Exibindo o trecho final do arquivo. Registros anteriores foram mantidos no app.','log-meta'));
    $('logsContent').append(card);
    const previous=positions.get(source.id),pre=card.querySelector('pre');
    pre.scrollTop=previous&&!previous.bottom?previous.top:pre.scrollHeight;
  }
}
async function refreshLogs(){
  clearTimeout(logTimer);
  if(logsBusy){logsPending=true;return;}
  if(activeTab!=='logs'||document.hidden)return;
  logsBusy=true;latestLogs=null;$('logsRefresh').disabled=true;
  $('logsDownload').disabled=true;$('logsDownload').dataset.unavailable='true';
  const source=$('logsSource').value,lines=$('logsLines').value;
  $('logsStatus').textContent='Consultando registros…';
  try{
    const data=await api('logs?source='+encodeURIComponent(source)+'&lines='+encodeURIComponent(lines));
    if(source===$('logsSource').value&&lines===$('logsLines').value){
      latestLogs=data;
      const signature=JSON.stringify(data);
      if(signature!==logsSignature){renderLogs(data);logsSignature=signature;}
      $('logsStatus').textContent='Atualizado às '+new Date().toLocaleTimeString()+' · Arandu Assistant '+data.version;
      $('logsDownload').disabled=false;delete $('logsDownload').dataset.unavailable;
    }
  }catch(error){logsSignature='';$('logsStatus').textContent=error.message;$('logsContent').replaceChildren(el('p','Não foi possível consultar os registros. Tente atualizar novamente.','log-error'));}
  finally{
    logsBusy=false;$('logsRefresh').disabled=false;
    const pending=logsPending;logsPending=false;
    if(activeTab==='logs'&&!document.hidden&&(pending||$('logsAuto').checked))logTimer=setTimeout(refreshLogs,pending?0:5000);
  }
}
$('logsRefresh').onclick=refreshLogs;
$('logsSource').onchange=refreshLogs;$('logsLines').onchange=refreshLogs;
$('logsAuto').onchange=()=>{$('logsAuto').checked?refreshLogs():clearTimeout(logTimer);};
document.addEventListener('visibilitychange',()=>{clearTimeout(logTimer);if(!document.hidden&&activeTab==='logs'&&$('logsAuto').checked)refreshLogs();});
$('logsDownload').onclick=()=>{
  if(!latestLogs)return;
  const text=['Arandu Assistant '+latestLogs.version,'Trechos finais dos logs; credenciais conhecidas ocultadas.','',...latestLogs.sources.flatMap(source=>['=== '+source.name+' · '+logState(source)+' ===',source.modified?'Última gravação: '+source.modified:'Sem gravação',source.last_error||'',source.error||source.text||'Sem registros.',''])].join('\n');
  const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'})),link=el('a');
  link.href=url;link.download='arandu-assistant-logs.txt';link.hidden=true;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('refresh').onclick=()=>task(refresh);
$('addPhrase').onclick=()=>{$('phraseRows').append(phraseRow({phrase:'',command:'',enabled:true}));dirty=true;};
$('savePhrases').onclick=()=>task(async()=>{
  const phrases=[...document.querySelectorAll('.phrase-row')].map(row=>({enabled:row.querySelector('[type=checkbox]').checked,phrase:row.querySelectorAll('[type=text]')[0].value,command:row.querySelectorAll('[type=text]')[1].value})).filter(row=>row.phrase||row.command);
  await api('phrases',{phrases});dirty=false;await refresh();notice('Frases salvas. Aplicadas na próxima conversa pela integração Arandu Assistant.');
});
$('closeConfig').onclick=()=>$('configDialog').close();
$('configForm').onsubmit=event=>{event.preventDefault();task(()=>saveSettings(false));};
$('saveRestart').onclick=()=>task(()=>saveSettings(true));
function applyTheme(value){if(value==='light'||value==='dark')document.documentElement.dataset.theme=value;else delete document.documentElement.dataset.theme;}
try{$('theme').value=localStorage.getItem('arandu-theme')||'system';}catch{}
applyTheme($('theme').value);
$('theme').onchange=()=>{applyTheme($('theme').value);try{localStorage.setItem('arandu-theme',$('theme').value);}catch{}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
task(refresh);

async function renderLlmManager(){
  const group=el('fieldset',undefined,'setting-section');group.append(el('legend','Modelos LLM e prontidão'));
  const status=el('p','Consultando modelos…');status.setAttribute('role','status');
  const list=el('div');group.append(status,list);$('configFields').append(group);
  let profiles={};
  async function update(){
    try{
      const result=await api('llm/models');profiles=result.profiles||{};
      status.textContent='Configurado: '+result.configured_model+'. Residente: '+(result.active_model||'nenhum confirmado')+'. A instalação não muda a seleção.';
      list.replaceChildren();
      for(const model of result.models){
        const row=el('div',undefined,'setting-row');row.append(el('span',model.name+' · '+(model.loaded?('carregado'+(model.integrity_verified?' e verificado':' · integridade não verificada')):model.installed===true?'instalado':model.installed===false?'não instalado':'instalação não verificada')));
        const button=el('button',model.installed?'Verificar pesos':'Instalar pesos ('+(model.id==='qwen3'?'640':'306')+' MB)','secondary');button.type='button';
        button.onclick=async()=>{button.disabled=true;status.textContent='Instalando/verificando '+model.name+'…';
          try{await api('llm/install',{model:model.id});await update();}catch(e){status.textContent=e.message;button.disabled=false;}};
        row.append(button);list.append(row);
      }
    }catch(e){status.textContent=e.message;}
  }
  const model=settingInput('llm_model');
  model?.addEventListener('change',()=>{
    const profile=profiles[model.value];
    if(profile)for(const [key,value] of Object.entries(profile)){const input=settingInput(key);if(input)input.value=String(value);}
    if(model.value==='qwen3'&&settingInput('contract_version').value==='1')settingInput('contract_version').value='2';
  });
  await update();
}
async function renderDecisionManager(){
  const group=el('fieldset',undefined,'setting-section');group.append(el('legend','Modelos Decision e processadores'));
  const status=el('p','Consultando prontidão…');status.setAttribute('role','status');const list=el('div');group.append(status,list);$('configFields').append(group);
  async function update(){
    try{
      const result=await api('decision/models');list.replaceChildren();
      status.textContent='Configurado: '+result.configured_model+' / '+result.configured_backend+'. Residente: '+(result.active_model||'nenhum')+' / '+(result.backend_used||'nenhum confirmado');
      for(const backend of result.backends||[])list.append(el('p',backend.id+': '+backend.status+' · '+(backend.reason||'Disponível')+(backend.validated?' · inferência GPU validada':'')));
      const rows=result.models?.length?result.models:[{id:'e5-small',name:'E5 Small'},{id:'minilm-multilingual',name:'MiniLM Multilingual'},{id:'e5-base',name:'E5 Base'}];
      for(const model of rows){
        const row=el('div',undefined,'setting-row');row.append(el('span',model.name+' · '+(model.license||'licença no manifesto')+' · '+(model.dimension||'?')+' dimensões · '+(model.disk_bytes?Math.ceil(model.disk_bytes/1e6)+' MB':'tamanho não verificado')+' · '+(model.loaded?'carregado e verificado':model.installed?'instalado':'instalação não verificada')));
        for(const [action,label] of [['install','Instalar/verificar pesos'],['prepare','Preparar GPU (exportação local)']]){
          const button=el('button',label,'secondary');button.type='button';button.onclick=async()=>{
            button.disabled=true;status.textContent=(action==='install'?'Instalando/verificando ':'Exportando IR local de ')+model.name+'…';
            try{await api('decision/'+action,{model:model.id,backend:action==='prepare'?'gpu_intel':'cpu'});await update();}
            catch(e){status.textContent=e.message;button.disabled=false;}
          };row.append(button);
        }list.append(row);
      }
    }catch(e){status.textContent=e.message;}
  }await update();
}
async function renderSttManager(){
  const group=el('fieldset',undefined,'setting-section stt-manager');group.append(el('legend','Pesos e modelo ativo'));
  const status=el('p','Consultando modelos…');status.setAttribute('role','status');group.append(status);
  const list=el('div');group.append(list);$('configFields').append(group);
  async function update(){
    if(!$('configDialog').open||editing?.id!=='stt'||!group.isConnected)return;
    try{
      const result=await api('stt/models?variant='+encodeURIComponent(settingInput('model_variant')?.value||'adapted'));
      const active=result.active;
      status.textContent=active?'Ativo no Assist: '+active.model+' · '+(active.model==='fastconformer'?active.variant:'multilíngue')+' · '+active.backend+' · CPU '+active.compute+' · '+active.threads+' threads · '+active.process_rss_mb.toFixed(0)+' MiB'+(Number.isFinite(active.recent?.inference_ms)?' · '+active.recent.inference_ms.toFixed(1)+' ms':''):'Modelo ativo ainda não confirmado.';
      list.replaceChildren();
      const labels={not_installed:'Não instalado',installed:'Instalado',downloading:'Baixando',verifying:'Verificando',loading:'Carregando',ready:'Pronto',error:'Erro'};
      for(const model of result.models){
        const line=el('div',undefined,'stt-model-row');line.append(el('span',model.name+(model.download_bytes?' · download '+(model.download_bytes/1000000).toFixed(0)+' MB':'')+' · '+(labels[model.state]||model.state)+(model.error?' · '+model.error:'')));
        const button=el('button',model.installed?'Verificar / reinstalar':'Baixar','secondary');button.type='button';button.disabled=['downloading','verifying','loading'].includes(model.state);
        button.onclick=async()=>{button.disabled=true;try{await api('stt/install',{model:model.id,variant:settingInput('model_variant')?.value||'adapted'});await update();}catch(e){status.textContent=e.message;button.disabled=false;}};line.append(button);list.append(line);
      }
      if(result.models.some(row=>['downloading','verifying','loading'].includes(row.state)))setTimeout(update,2000);
    }catch(e){status.textContent=e.message;}
  }
  const link=el('a','Testar STT no Home Assistant');link.href='/arandu-ai-lab#stt';link.target='_top';group.append(link);
  settingInput('model_variant').addEventListener('change',update);await update();
}
