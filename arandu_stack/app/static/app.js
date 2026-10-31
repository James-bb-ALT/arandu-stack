'use strict';
let state=null, editing=null, dirty=false;
let activeTab='overview',logTimer=null,logsBusy=false,logsPending=false,latestLogs=null,logsSignature='';
const $=id=>document.getElementById(id);
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function api(path,body){
  const response=await fetch('api/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Arandu-CSRF':state?.csrf||''},body:body===undefined?undefined:JSON.stringify(body)});
  let data;try{data=await response.json();}catch(e){throw Error(response.status===403?'Acesso recusado. Atualize a página e confira as permissões do app.':'O serviço não retornou uma resposta válida.');}
  if(!response.ok)throw Error(data.error||'Não foi possível concluir.');return data;
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
    restart.onclick=()=>task(async()=>{await api('modules/'+module.id+'/restart',{});await refresh();});
    [toggle,config,restart].forEach(b=>{if(!module.installed){b.disabled=true;b.dataset.unavailable='true';}actions.append(b);});
    const logs=el('button',module.state==='error'?'Ver erro':'Logs','secondary');
    logs.onclick=()=>{ $('logsSource').value=module.id; selectTab('logs'); };
    actions.append(logs);
    card.append(actions,el('div',module.slug||'Módulo incluído no Arandu Assistant.','slug'));$('cards').append(card);
    if(module.last_error)card.append(el('p',module.last_error,'log-error'));
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
  const apply=$('saveRestart');apply.disabled=module.state!=='started';
  apply.title=apply.disabled?'Ative o módulo para aplicar imediatamente':'Salva as opções e reinicia somente este módulo';
  if(apply.disabled)apply.dataset.unavailable='true';else delete apply.dataset.unavailable;
  updateDependencies();$('configDialog').showModal();
}
function settingInput(name){return $('configFields').querySelector('[data-setting="'+name+'"]');}
function updateDependencies(){
  for(const field of editing?.schema||[]){
    const row=$('configFields').querySelector('[data-field="'+field.id+'"]'),input=settingInput(field.id);
    const active=!field.depends_on||!!settingInput(field.depends_on)?.checked;
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
  try{
    await api('modules/'+editing.id+'/options',{options:settingsPayload()});
    if(restart&&editing.state==='started')await api('modules/'+editing.id+'/restart',{});
  }catch(error){$('configError').textContent=error.message;return;}
  $('configDialog').close();await refresh();
  notice(restart?'Configuração salva e módulo reiniciado.':'Configuração salva. Reinicie o módulo para aplicar.');
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
