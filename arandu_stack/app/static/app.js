'use strict';
let state=null, editing=null, dirty=false;
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
  state=await api('state');renderModules();if(!dirty)renderPhrases();
  notice(state.local?'Prévia local · os motores Linux estão incluídos no app Arandu Assistant.':'Estado atualizado. Todos os módulos pertencem ao mesmo app.');
}
function renderModules(){
  $('cards').replaceChildren();
  state.modules.forEach((module,index)=>{
    const card=el('article',undefined,'card'),top=el('div',undefined,'card-top');
    top.append(el('span',String(index+1).padStart(2,'0'),'card-num'));
    const on=module.state==='started';
    top.append(el('span',module.state==='error'?'Falha':on?'Ativo':module.installed?'Desativado':'Runtime indisponível','status'+(on?' on':'')));
    card.append(top,el('h3',module.name),el('p',module.description));
    const actions=el('div',undefined,'card-actions');
    const toggle=el('button',on?'Desativar':'Ativar',on?'secondary':'');
    toggle.onclick=()=>task(async()=>{notice('Aplicando alteração em '+module.name+'…');await api('modules/'+module.id+'/'+(on?'stop':'start'),{});await refresh();});
    const config=el('button','Configurar','secondary');config.onclick=()=>openSettings(module);
    const restart=el('button','Reiniciar','secondary');
    restart.onclick=()=>task(async()=>{await api('modules/'+module.id+'/restart',{});await refresh();});
    [toggle,config,restart].forEach(b=>{if(!module.installed){b.disabled=true;b.dataset.unavailable='true';}actions.append(b);});
    card.append(actions,el('div',module.slug||'Módulo incluído no Arandu Assistant.','slug'));$('cards').append(card);
  });
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
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));['modules','phrases','help'].forEach(id=>$(id).hidden=id!==button.dataset.tab);});
$('refresh').onclick=()=>task(refresh);
$('addPhrase').onclick=()=>{$('phraseRows').append(phraseRow({phrase:'',command:'',enabled:true}));dirty=true;};
$('savePhrases').onclick=()=>task(async()=>{
  const phrases=[...document.querySelectorAll('.phrase-row')].map(row=>({enabled:row.querySelector('[type=checkbox]').checked,phrase:row.querySelectorAll('[type=text]')[0].value,command:row.querySelectorAll('[type=text]')[1].value})).filter(row=>row.phrase||row.command);
  await api('phrases',{phrases});dirty=false;await refresh();notice('Frases salvas. Aplicadas na próxima conversa pela integração Arandu Assistant.');
});
$('closeConfig').onclick=()=>$('configDialog').close();
$('configForm').onsubmit=event=>{event.preventDefault();task(()=>saveSettings(false));};
$('saveRestart').onclick=()=>task(()=>saveSettings(true));
task(refresh);
