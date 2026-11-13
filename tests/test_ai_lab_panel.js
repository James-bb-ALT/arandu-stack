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
const vmUrls = [];
vm.runInNewContext(fs.readFileSync('custom_components/local_nlu/ai_lab_panel.js','utf8'), {
  HTMLElement:class {}, customElements:{get:()=>undefined,define:(_name,cls)=>{Panel=cls;}},
  document:{createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
  atob: (s) => {
    if (!/^[A-Za-z0-9+/=]+$/.test(s) || s.length % 4 !== 0) throw new Error('Invalid base64');
    return Buffer.from(s, 'base64').toString('latin1');
  },
  Blob: globalThis.Blob,
  Uint8Array: globalThis.Uint8Array,
  clearInterval: globalThis.clearInterval,
  clearTimeout: globalThis.clearTimeout,
  URL: {
    createObjectURL: () => { const u = `blob:vm-${vmUrls.length + 1}`; vmUrls.push(u); return u; },
    revokeObjectURL: (u) => { vmUrls.push(`revoked:${u}`); },
  },
});
const text=node=>node.textContent+' '+(node.children || []).map(text).join(' ');

test('Decision matrix exports metrics without raw commands, catalog or residential IDs',()=>{
  const panel=new Panel();panel.decisionMatrix={repetitions:3,warmup:1,rows:[{text:'private speech',target_entities:['fan.private']}],
    metrics:{'e5-small:cpu':{samples:3,labelled:3,correct_plan:3,status:'pass',reason:'private speech',latency_ms:{p50:1,p95:2,p99:3},peak_rss_bytes:1048576}}};
  const data=panel.decisionMatrixData(),serialized=JSON.stringify(data);
  assert.equal(data.home_assistant_actions,0);assert.equal(data.rows[0].p95_ms,2);
  assert.ok(!serialized.includes('private'));assert.ok(!serialized.includes('target_entities'));
});

test('Decision matrix renders unsupported GPU without fabricating latency',()=>{
  const panel=new Panel(),nodes={};panel.$=id=>nodes[id] ||= new Element();
  panel.decisionMatrix={metrics:{'e5-small:gpu_intel':{status:'unsupported',samples:0,labelled:0,latency_ms:{},cold_start_ms:0}}};
  panel.renderDecisionMatrix();const rendered=text(nodes.matrixOutput);
  assert.match(rendered,/unsupported/);assert.match(rendered,/não medido/);assert.match(rendered,/—/);
});
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

test('LLM v2 exposes typed plan and measured generation without execution claims',()=>{
  const result=fixture();result.source='llm';result.operations_validated=true;
  result.plan={version:2,type:'automation',operations:[{action:'brightness',entity_id:'light.fixture',parameters:{value:40},evidence:0}]};
  result.stages.llm={status:'executed',output:{contract_version:2,model:'tucano',kind:'automation',plan:result.plan,
    first_token_ms:1200,generated_tokens:80,tokens_per_second:20,memory_bytes:104857600,load_ms:0,inference_ms:4000,
    raw_json_validated:true,validated_json:result.plan,retrieval:{ranking:[{entity_id:'light.fixture'}]}}};
  const nodes=render(result);
  assert.match(text(nodes.interpretations),/LLM e contrato.*tucano.*v2/);
  assert.match(text(nodes.interpretations),/"value":40/);
  assert.match(text(nodes.interpretations),/1,20 s.*80 tokens.*20.0 tokens\/s/);
  assert.match(text(nodes.answer),/simulação/);
});

test('LLM exports omit private text, device names, IDs, raw JSON and credentials',()=>{
  const panel=new Panel();panel.llmComparison={metrics:{tucano:{samples:1}},rows:[{
    case:0,text:'PRIVATE_COMMAND',expected_plan:{entity_id:'PRIVATE_ENTITY'},actual:{name:'PRIVATE_NAME'},model:'tucano',correct:true,
    result:{kind:'automation',raw_json:{secret:'PRIVATE_TOKEN'},generated_tokens:80,inference_ms:2000}}]};
  const data=panel.llmExportData();
  assert.doesNotMatch(JSON.stringify(data),/PRIVATE|raw_json|expected_plan|entity_id/);
  assert.equal(data.rows[0].generated_tokens,80);
});

test('LLM v2 test utterances are not automatically persisted in local history',()=>{
  const panel=new Panel();panel.last={text:'PRIVATE',stages:{llm:{output:{contract_version:2}}}};
  panel.store=()=>{throw Error('must not persist');};panel.persist();
});

test('algorithm comparison uses authenticated HA websocket and selected area',async()=>{
  const panel=new Panel();let sent;
  panel.$=id=>({value:({decisionAlgorithm:'baseline-v2',testArea:'sala',threshold:'.8',margin:'.08'})[id] || ''});
  panel._hass={callWS:async body=>{sent=body;return {dry_run:true};}};
  await panel.call('decision_compare',{examples:[{text:'Liga a luz'}]});
  assert.equal(sent.type,'local_nlu/ai_lab');
  assert.equal(sent.payload.algorithm,'baseline-v2');
  assert.equal(sent.payload.test_area_id,'sala');
  assert.equal(sent.payload.command,'decision_compare');
  assert.equal(sent.payload.execute,undefined);
});

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


test('cached native panel remounts STT after reconnecting',()=>{
  const panel=new Panel();let mounted=0;
  panel.ready=true;panel._hass={};panel.sttDisposed=true;panel.disconnected=true;
  panel.mountStt=()=>mounted++;
  panel.connectedCallback();
  assert.equal(panel.disconnected,false);
  assert.equal(mounted,1);
});

test('general LLM answer is displayed as speech without an execution claim',()=>{
  const value=fixture();
  value.source='llm';value.response_text='A capital da França é Paris.';
  value.operations_validated=false;value.confirmation_required=false;value.sensitive=false;
  value.proposal={intent:'conversation',target_entities:[],requires_clarification:false};
  value.stages.llm={status:'executed',output:{proposal:value.proposal,response_text:value.response_text}};
  const nodes=render(value);
  assert.equal(nodes.answer.textContent,value.response_text);
});

test('structured Decision shows fields, cache and separate scores without inventing certainty',()=>{
  const value=fixture();
  value.stages.decision={status:'executed',output:{algorithm:'structured-v3',decision_status:'uncalibrated',
    analysis:{action:{text:'faz funcionar',exact:null,evidence:null},device:{eligible_count:2,excluded_count:1,candidates:[]},area:{source:'previous'}},
    ranking:[{intent:'turn_on',entity_id:'fan.fixture',raw_score:.77,intent_score:.81,target_score:.73}],
    cache:{descriptor_hits:3,descriptor_misses:0,query_hits:2,query_misses:0},queue_ms:1,load_ms:0,retrieval_ms:2,inference_ms:3,validation_ms:1}};
  const nodes=render(value), rendered=text(nodes.interpretations);
  assert.match(rendered,/structured-v3/);
  assert.match(rendered,/Descritores: 3 acertos/);
  assert.match(rendered,/Intenção: 0.8100/);
  assert.match(rendered,/Entidade: 0.7300/);
  assert.match(rendered,/sem interpretação como probabilidade/);
});

function makeTtsPanel({playFn, ttsFn} = {}) {
  const nodes = {
    tts: {value: 'piper'},
    player: {src: '', play: playFn || (async () => {}), pause() {}, load() {}, removeAttribute() {}},
    playAudio: {disabled: true},
    ttsStatus: {textContent: ''},
    keepContext: {checked: false},
    worker: {value: 'http://127.0.0.1:11555'},
    entry: {value: 'entry-1'},
    ttsEndpoint: {value: ''},
    ttsToken: {value: ''},
    decision: {value: 'e5-small'},
    mode: {value: 'pipeline'},
    llm: {value: 'tucano'},
    testArea: {value: ''},
    threshold: {value: '0.8'},
    margin: {value: '0.08'},
  };
  const revoked = [];
  const prevCreate = URL.createObjectURL;
  const prevRevoke = URL.revokeObjectURL;
  let seq = 0;
  URL.createObjectURL = () => `blob:test-${++seq}`;
  URL.revokeObjectURL = (u) => revoked.push(u);
  const p = new Panel();
  p.shadowRoot = {getElementById: (id) => nodes[id] || {textContent: '', replaceChildren() {}, append() {}, disabled: false}};
  p.render = () => {};
  p.persist = () => {};
  p.last = fixture();
  p.last.response_text = 'Luz acesa com sucesso.';
  p.call = ttsFn || (async () => ({audio: Buffer.from('RIFF\x00\x00\x00\x00WAVEfmt ').toString('base64'), model: 'piper', latency_ms: 42}));
  return {
    panel: p,
    nodes,
    revoked,
    restore() { URL.createObjectURL = prevCreate; URL.revokeObjectURL = prevRevoke; },
  };
}

test('TTS NotAllowedError preserves synthesized audio and sets blocked_by_browser without synthesis error', async () => {
  let ttsCalls = 0;
  let shouldBlock = true;
  const err = new Error('play() failed because the user didn\'t interact with the document first.');
  err.name = 'NotAllowedError';
  const ctx = makeTtsPanel({
    playFn: async () => { if (shouldBlock) throw err; },
    ttsFn: async () => { ttsCalls++; return {audio: Buffer.from('RIFF0000WAVE').toString('base64'), model: 'pocket', latency_ms: 30}; },
  });
  try {
    await ctx.panel.speak();
    assert.equal(ttsCalls, 1);
    assert.equal(ctx.panel.ttsState, 'ready');
    assert.equal(ctx.panel.playbackState, 'blocked_by_browser');
    assert.equal(ctx.panel.last.stages.tts.status, 'executed');
    assert.equal(ctx.panel.last.stages.tts.playback_state, 'blocked_by_browser');
    assert.equal(ctx.nodes.playAudio.disabled, false);
    assert.ok(ctx.panel.audioUrl);
    // Manual play reuses existing WAV without re-synthesizing
    shouldBlock = false;
    const played = await ctx.panel.playAudio({autoplay: false});
    assert.equal(played, true);
    assert.equal(ttsCalls, 1);
    assert.equal(ctx.panel.playbackState, 'playing');
  } finally {
    ctx.restore();
  }
});

test('TTS authorized autoplay plays cleanly and updates states', async () => {
  const ctx = makeTtsPanel();
  try {
    await ctx.panel.speak();
    assert.equal(ctx.panel.ttsState, 'ready');
    assert.equal(ctx.panel.playbackState, 'playing');
    assert.equal(ctx.panel.last.stages.tts.status, 'executed');
    assert.equal(ctx.panel.last.stages.tts.synthesis_state, 'ready');
  } finally {
    ctx.restore();
  }
});

test('TTS HTTP 502 and invalid base64 mark synthesis_error and preserve interpretation', async () => {
  const ctx502 = makeTtsPanel({ttsFn: async () => { throw new Error('HTTP 502 Bad Gateway'); }});
  try {
    await assert.rejects(() => ctx502.panel.speak(), /HTTP 502/);
    assert.equal(ctx502.panel.ttsState, 'synthesis_error');
    assert.equal(ctx502.panel.last.stages.tts.status, 'error');
    assert.equal(ctx502.panel.last.text, 'FIXTURE_TECNICA');
  } finally {
    ctx502.restore();
  }
  const ctxBad = makeTtsPanel({ttsFn: async () => ({audio: '!!!not-base64!!!', model: 'piper'})});
  try {
    await assert.rejects(() => ctxBad.panel.speak(), /inválido/);
    assert.equal(ctxBad.panel.ttsState, 'synthesis_error');
    assert.equal(ctxBad.panel.last.stages.tts.status, 'error');
  } finally {
    ctxBad.restore();
  }
});

test('TTS skips simple action without speech and errors on empty conversation', async () => {
  const ctx = makeTtsPanel();
  try {
    ctx.panel.last.response_text = '';
    ctx.panel.last.clarification_question = null;
    await ctx.panel.speak();
    assert.equal(ctx.panel.last.stages.tts.status, 'skipped');
    ctx.panel.last.kind = 'conversation';
    ctx.panel.last.response_text = '   ';
    await assert.rejects(() => ctx.panel.speak(), /llm_conversation_empty/);
    assert.equal(ctx.panel.ttsState, 'synthesis_error');
  } finally {
    ctx.restore();
  }
});

test('TTS consecutive speak revokes previous objectURL and ignores stale response; disconnect cancels', async () => {
  let resolveFirst;
  let callNum = 0;
  const ctx = makeTtsPanel({
    ttsFn: () => {
      callNum++;
      if (callNum === 1) return new Promise((r) => { resolveFirst = r; });
      return Promise.resolve({audio: Buffer.from('RIFF2222WAVE').toString('base64'), model: 'pocket', latency_ms: 15});
    },
  });
  try {
    const first = ctx.panel.speak();
    const second = ctx.panel.speak();
    await second;
    assert.equal(ctx.panel.last.stages.tts.model, 'pocket');
    const urlAfterSecond = ctx.panel.audioUrl;
    resolveFirst({audio: Buffer.from('RIFF1111WAVE').toString('base64'), model: 'piper', latency_ms: 99});
    await first;
    // Stale first response must not overwrite second
    assert.equal(ctx.panel.audioUrl, urlAfterSecond);
    assert.equal(ctx.panel.last.stages.tts.model, 'pocket');
    // Disconnecting revokes active URL
    ctx.panel.disconnectedCallback();
    assert.ok(vmUrls.includes(`revoked:${urlAfterSecond}`));
    assert.equal(ctx.panel.audioUrl, null);
  } finally {
    ctx.restore();
  }
});
