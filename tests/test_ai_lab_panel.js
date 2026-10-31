/* FIXTURE_TECNICA: render real panel methods with a bounded DOM fake. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
  constructor() { this.children=[]; this.textContent=''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; this.textContent=''; }
}
let Panel;
vm.runInNewContext(fs.readFileSync('custom_components/local_nlu/ai_lab_panel.js','utf8'), {
  HTMLElement:class {}, customElements:{get:()=>undefined,define:(_name,cls)=>{Panel=cls;}},
  document:{createElement:()=>new Element(),createTextNode:text=>({textContent:text})}
});
const text=node=>node.textContent+' '+(node.children || []).map(text).join(' ');
function render(result) {
  const panel=new Panel(),nodes={};
  panel.$=id=>nodes[id] ||= new Element();
  panel.executionState=()=>{};
  panel.render(result);
  return nodes;
}
function fixture() {
  return {text:'FIXTURE_TECNICA',total_ms:2,dry_run:true,operations_validated:false,
    catalog:{entities:[{entity_id:'light.fixture',name:'Luz <fixture>',area_id:'sala'}],areas:[{area_id:'sala',names:['Sala']}]},
    stages:Object.fromEntries(['vad','stt','nlu','decision','llm','ha','tts'].map(n=>[n,{status:'skipped'}]))};
}

test('duration switches units at one second and handles absent metrics',()=>{
  const panel=new Panel();
  for(const [input,expected] of [[0,'0,0 ms'],[12.34,'12,3 ms'],[999,'999,0 ms'],
      [1000,'1,00 s'],[1500,'1,50 s'],[28225,'28,23 s'],[null,'Indisponível'],
      [undefined,'Indisponível'],[NaN,'Indisponível'],[Infinity,'Indisponível']]) {
    assert.equal(panel.duration(input),expected);
  }
  assert.equal(panel.duration(null,'—'),'—');
});

test('stage and total durations use automatic units without modifying raw data',()=>{
  const result=fixture();
  result.total_ms=1750;
  result.stages.decision={status:'executed',latency_ms:450};
  result.stages.llm={status:'executed',latency_ms:1300};
  const before=JSON.stringify(result),nodes=render(result);
  assert.match(text(nodes.metrics),/1,75 s/);
  assert.match(text(nodes.stages),/Decision · executado · 450,0 ms/);
  assert.match(text(nodes.stages),/LLM · executado · 1,30 s/);
  assert.equal(JSON.stringify(result),before);
});
test('ranking without calibrated acceptance remains a hypothesis',()=>{
  const result=fixture();
  result.stages.decision={status:'executed',output:{decision_status:'uncalibrated',reason:'joint_calibration_missing',ranking:[{entity_id:'light.fixture',intent:'turn_on',raw_score:.99}]}};
  const nodes=render(result);
  assert.match(text(nodes.interpretations),/Hipótese: ligar.*Luz <fixture>/);
  assert.match(text(nodes.interpretations),/não autoriza uma ação/);
  assert.match(text(nodes.finalAction),/Executaria a ação\?.*Não/);
  assert.match(text(nodes.ranking),/0.9900/);
});
test('typed input explains VAD and STT and keeps TTS failure separate',()=>{
  const result=fixture();
  result.stages.tts={status:'error',error:'http_502',reason:'Falha no áudio'};
  result.operations_validated=true;
  result.source='llm';
  result.proposal={intent:'turn_on',target_entities:['light.fixture']};
  const nodes=render(result);
  assert.match(text(nodes.stages),/VAD · não necessário — entrada de texto/);
  assert.match(text(nodes.stages),/STT · não necessário — texto digitado/);
  assert.match(text(nodes.answer),/Não será executada enquanto a simulação estiver ativa/);
  assert.match(text(nodes.finalAction),/Falha de áudio; o resultado da interpretação permanece/);
  assert.match(text(nodes.finalAction),/Luz <fixture> · light.fixture · Sala/);
});

test('three-part analysis and hypothesis never imply permission to execute',()=>{
  const result=fixture();
  result.operations_validated=true;result.confirmation_required=true;result.source='llm';
  result.proposal={intent:'turn_on',target_entities:['light.fixture']};
  result.clarification_question='Acho que você gostaria de ligar Luz <fixture>. É isso?';
  result.stages.decision={status:'executed',output:{decision_status:'uncalibrated',analysis:{action:{text:'lga',ranking:[{intent:'turn_on',semantic_similarity:.7}]},device:{name:'Luz <fixture>'},area:{area_id:'sala',source:'origin'}}}};
  const nodes=render(result);
  assert.match(text(nodes.interpretations),/Ação analisada.*lga.*similaridade 0.7000/);
  assert.match(text(nodes.interpretations),/Dispositivo analisado.*Luz <fixture>/);
  assert.match(text(nodes.interpretations),/Área analisada.*Sala.*origem da conversa/);
  assert.match(text(nodes.answer),/É isso\?.*aguardando confirmação/);
  assert.match(text(nodes.finalAction),/Executaria a ação\?.*Não.*Aguarda confirmação/);
});

test('typed test without origin displays real ambiguity and offers area context',()=>{
  const result=fixture();
  result.test_context={origin_area:null,source:'none'};
  result.stages.decision={status:'executed',output:{decision_status:'ambiguous',reason:'insufficient_margin',analysis:{action:{text:'leva'},device:{candidates:[{entity_id:'fan.a',name:'Ventilador A',area_id:'sala'},{entity_id:'fan.b',name:'Ventilador B'}]},area:{source:'unknown'}}}};
  const nodes=render(result);
  assert.match(text(nodes.interpretations),/Sem área e sem satélite/);
  assert.match(text(nodes.interpretations),/Ambíguo: Ventilador A · Sala; Ventilador B/);
  assert.match(text(nodes.interpretations),/qual parte está ambígua/);
});
