"""Typed UI and validation metadata for the six internal modules."""
from pathlib import Path
import re

WAKE_WORDS = [('alexa', 'Alexa'), ('hey_jarvis', 'Hey Jarvis'), ('hey_mycroft', 'Hey Mycroft'),
              ('hey_rhasspy', 'Hey Rhasspy'), ('okay_nabu', 'Okay Nabu')]
VOICE_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')

def field(name, label, kind, section='Geral', **extra):
    return dict(id=name, label=label, type=kind, section=section, **extra)

DOWNLOAD = field('auto_download', 'Baixar modelos e vozes automaticamente', 'switch', help='Quando ativado, o módulo pode baixar arquivos ao iniciar. Desative para usar somente arquivos locais.')
THREADS = field('threads', 'Threads de CPU', 'number', 'Desempenho', min=1, max=4, step=1)
WAITING = field('queue_limit', 'Pedidos em espera', 'number', 'Desempenho', min=0, max=16, step=1, help='Uma inferência ativa por motor. Pedidos acima da fila são recusados explicitamente.')
FIELDS = {
 'wake': [field('wake_word', 'Palavra de ativação', 'select', choices=WAKE_WORDS, help='Esta seleção vale para todas as conexões do serviço interno. Recarregue o motor Wake no Assist após mudar.'),
          field('threshold', 'Sensibilidade (threshold)', 'number', min=.01, max=.99, step=.01, help='Valores menores detectam com mais facilidade, mas podem gerar falsos positivos.'),
          field('trigger_level', 'Ativações consecutivas (trigger level)', 'number', min=1, max=20, step=1),
          field('refractory_seconds', 'Intervalo entre detecções (segundos)', 'number', min=0, max=60, step=.1)],
 'nlu': [field('custom_phrases_enabled', 'Usar frases personalizadas', 'switch', help='Os ajustes do NLU exigem a integração HACS atualizada e o endpoint do painel configurado.'),
         field('max_input_chars', 'Limite de caracteres por comando', 'number', min=64, max=512, step=1),
         field('session_ttl', 'Duração do contexto da conversa (segundos)', 'number', min=1, max=600, step=1),
         field('low_battery_threshold', 'Aviso de bateria baixa (%)', 'number', min=0, max=100, step=1)],
 'decision': [DOWNLOAD, THREADS, WAITING,
              field('model','Modelo Decision','select',choices=[('e5-small','E5 Small · padrão'),('minilm-multilingual','MiniLM Multilingual L12'),('e5-base','E5 Base')]),
              field('backend','Processador Decision','select',choices=[('cpu','CPU · PyTorch'),('gpu_intel','GPU Intel · requer validação OpenVINO')],help='GPU explícita indisponível falha sem trocar para CPU. Prepare o modelo e confirme a carga antes de aplicar.'),
              field('resident_policy','Política de RAM (Residency)','select','Pipeline inteligente',choices=[('RESIDENT','RESIDENT · manter carregado em RAM'),('LAZY','LAZY · carregar sob demanda'),('OFF','OFF · fast-path determinístico sem pesos')],help='RESIDENT mantém o modelo em RAM para zero cold-start por pedido. LAZY carrega na primeira inferência. OFF não carrega pesos.'),
              field('auto_resolve_level','Nível de auto-resolução','select','Pipeline inteligente',choices=[('conservative','Conservador · exige confirmação se não calibrado'),('calibrated','Calibrado · somente após calibração válida')],help='Hipóteses não calibradas exigem confirmação.'),
              field('algorithm', 'Algoritmo de interpretação', 'select', 'Decisão', choices=[('structured-v3','Decision 2.0 · intenção, alvo e área'),('baseline-v2','Compatibilidade · Decision 1.0')], help='Calibrações são separadas por algoritmo. Comparação no AI Lab nunca executa ações.'),
              field('threshold', 'Confiança mínima calibrada', 'number', 'Decisão', min=.5, max=1, step=.01, help='Aceitação semântica automática exige confiança conjunta calibrada >=80% e margem. Similaridade não é certeza. Ação explícita/alvo único usam validação determinística; hipóteses não calibradas exigem confirmação.'),
              field('min_margin', 'Margem mínima entre candidatos', 'number', 'Decisão', min=.001, max=1, step=.001)],
 'llm': [DOWNLOAD, THREADS, WAITING,
         field('llm_model','Modelo LLM','select',choices=[
             ('tucano','Tucano 2 0.5B · Q4_K_M'),
             ('qwen3','Qwen3 0.6B · Q8_0 experimental'),
             ('lfm2.5-1.2b','LFM2.5-1.2B-Instruct · QAD-Q4_0 (recomendado)'),
             ('lfm3-1.7b','LFM3-1.7B · QAD-Q4_0'),
             ('qwen3-1.7b','Qwen3-1.7B · Q4_K_M experimental'),
         ],help='Instale os pesos individualmente. Aplicação verifica a carga e restaura o modelo anterior em falha.'),
         field('resident_policy', 'Política de RAM do LLM', 'select', 'Desempenho',
               choices=[('RESIDENT', 'RESIDENT · pré-carregar e manter na RAM'),
                        ('LAZY', 'LAZY · carregar ao usar e manter aquecido'),
                        ('OFF', 'OFF · não carregar nem usar o LLM')],
               help='Somente o modelo LLM selecionado. RESIDENT evita cold start ao iniciar; não gera tokens em segundo plano.'),
         field('contract_version','Contrato de automação','select',choices=[(2,'LLM 2.0 · operações e parâmetros tipados'),(1,'Compatibilidade · LLM 1.0 / Tucano')]),
         field('context_size', 'Janela de contexto (tokens)', 'select', 'Geração', choices=[(2048,'2.048'),(4096,'4.096'),(8192,'8.192')]),
         field('max_tokens', 'Limite da resposta (tokens)', 'number', 'Geração', min=64, max=512, step=1, help='Use 384 para planos v2 maiores; 192 ou 256 no contrato legado. Limites baixos, como 64, podem interromper a resposta; nenhuma ação parcial é aceita. Salve e aplique o modelo.'),
         field('temperature', 'Temperatura', 'number', 'Geração', min=0, max=1, step=.05, help='Zero produz respostas mais previsíveis.')],
 'stt': [field('stt_model', 'Motor de reconhecimento', 'select', choices=[
             ('fastconformer','Arandu FastConformer'),
             ('whisper_tiny','Whisper Tiny'),
             ('whisper_base','Whisper Base'),
             ('whisper_small','Whisper Small'),
             ('parakeet_ptbr_30s', 'Parakeet TDT V3 pt-BR 30s'),
             ('parakeet_tagarela', 'Parakeet TAGARELA pt-BR'),
             ('orukeet_int8', 'Orukeet INT8'),
             ('granite_turboctc_q4', 'Granite TurboCTC Q4_K_M'),
             ('parakeet_ultra_q8', 'Parakeet Ultra Q8_0 (recomendado)'),
             ('parakeet_ultra_q4', 'Parakeet Ultra Q4_K_M (variante)'),
             ('phonon_2_5', 'Phonon 2.5 (CPU compacto)'),
             ('nemotron_unified_0_6b', 'NVIDIA Nemotron Unified ASR 0.6B (experimental)'),
         ]),
         field('nemotron_streaming_profile', 'Perfil streaming Nemotron', 'select', 'Desempenho',
               choices=[('precision','Precisão (560 ms / 560 ms)'),('balanced','Balanceado (160 ms / 400 ms)'),('fast','Rápido (80 ms / 160 ms)'),('experimental','Experimental (80 ms / 80 ms)')]),
         field('whisper_profile', 'Correção dos motores de vocabulário aberto', 'select', choices=[('normalize_only','Somente normalização'),('raw','Sem correção'),('full','Completo · vocabulário aberto')]),
         field('model_variant', 'Variante do FastConformer', 'select', choices=[('adapted','Arandu adaptado'),('original','FastConformer original')]),
         DOWNLOAD, THREADS,
         field('resident_policy', 'Política de RAM', 'select', 'Desempenho',
               choices=[('RESIDENT', 'RESIDENT · pré-carregar e manter na RAM'),
                        ('LAZY', 'LAZY · carregar no primeiro áudio e manter aquecido'),
                        ('OFF', 'OFF · não carregar nem transcrever')],
               help='RESIDENT pré-carrega na inicialização. LAZY carrega no primeiro áudio. OFF mantém o serviço acessível sem carregar pesos nem transcrever.'),
         field('max_clients', 'Conexões de áudio simultâneas', 'number', 'Desempenho', min=1, max=32, step=1, help='Limita capturas e conexões ociosas. A inferência STT continua serializada.'),
         field('padding_ms', 'Áudio adicional nas bordas', 'select', 'Áudio', choices=[(0,'Sem padding'),(100,'100 ms'),(200,'200 ms'),(300,'300 ms')]),
         field('vad_enabled', 'Habilitar Silero VAD', 'switch', 'STT — Processamento de áudio', help='Detecta início/fim de fala com ONNX CPU; mantém produção desativada até escolha explícita.'),
         field('vad_threshold', 'Threshold do VAD', 'number', 'STT — Processamento de áudio', min=.05, max=.95, step=.05),
         field('vad_min_speech_ms', 'Fala mínima VAD (ms)', 'number', 'STT — Processamento de áudio', min=50, max=2000, step=10),
         field('vad_min_silence_ms', 'Silêncio mínimo VAD (ms)', 'number', 'STT — Processamento de áudio', min=100, max=3000, step=50),
         field('vad_pre_roll_ms', 'Pre-roll VAD (ms)', 'number', 'STT — Processamento de áudio', min=0, max=1000, step=50),
         field('vad_post_roll_ms', 'Post-roll VAD (ms)', 'number', 'STT — Processamento de áudio', min=0, max=1000, step=50),
         field('high_pass_filter', 'Habilitar High-Pass Filter', 'switch', 'STT — Processamento de áudio'),
         field('noise_suppression', 'Habilitar Noise Suppression (WebRTC)', 'switch', 'STT — Processamento de áudio'),
         field('ns_level', 'Nível de redução de ruído (0–3)', 'number', 'STT — Processamento de áudio', min=0, max=3, step=1),
         field('auto_gain_control', 'Habilitar AGC2 (WebRTC)', 'switch', 'STT — Processamento de áudio'),
         field('agc_gain_db', 'Ganho AGC2 (dB)', 'number', 'STT — Processamento de áudio', min=-30, max=30, step=1),
         field('echo_cancellation', 'Habilitar AEC3 quando houver referência', 'switch', 'STT — Processamento de áudio', help='Só ativa quando a fonte fornecer áudio far-end real; sem referência é marcado como indisponível.'),
         field('profile', 'Correção do FastConformer', 'select', 'Reconhecimento', choices=[('full','Completo'),('raw','Sem correção'),('normalize_only','Somente normalização'),('acoustic_only','Somente acústico'),('fuzzy_only','Somente aproximação'),('inventory_only','Somente inventário'),('legacy_full','Completo legado')]),
         field('lexical_enabled', 'Usar correção lexical', 'switch', 'Reconhecimento'),
         field('closed_vocabulary', 'Limitar ao vocabulário conhecido', 'switch', 'Reconhecimento'),
         field('generic_weight', 'Peso do vocabulário geral', 'number', 'Reconhecimento', min=0,max=100,step=1),
         field('home_assistant_weight', 'Peso dos nomes do Home Assistant', 'number', 'Reconhecimento', min=0,max=100,step=1),
         field('refresh_inventory', 'Sincronizar nomes e áreas do HA', 'switch', 'Contexto', help='Permite ao STT consultar o inventário pela API do Home Assistant.'),
         field('inventory_refresh_seconds', 'Intervalo de atualização do inventário (segundos)', 'number', 'Contexto', min=0,max=86400,step=1, help='Zero desativa a atualização periódica.'),
         field('debug_text', 'Registrar textos reconhecidos para diagnóstico', 'switch', 'Diagnóstico', help='Os textos ficam nos logs do módulo; mantenha desligado no uso normal.')],
 'tts': [field('voice', 'Voz principal', 'select', help='Arandu e Faber Medium já acompanham o app. Define o padrão do serviço; uma voz escolhida explicitamente no Assist tem prioridade.'),
         DOWNLOAD, WAITING,
         field('resident_policy', 'Política de RAM', 'select', 'Desempenho',
               choices=[('RESIDENT', 'RESIDENT · pré-carregar voz principal na RAM'),
                        ('LAZY', 'LAZY · carregar na primeira síntese e reutilizar'),
                        ('OFF', 'OFF · não carregar nem sintetizar')],
               help='Controla a residência da voz principal selecionada (Piper ou Pocket).'),
         field('piper_thread_budget', 'Limitar threads do Piper (experimental)', 'switch', 'Desempenho', help='Pode reduzir uso de CPU e aumentar a latência. Desligado mantém o gerenciamento de threads original do Piper.'),
         field('cpu_threads', 'Limite experimental de threads do Piper', 'number', 'Desempenho', min=1,max=4,step=1, depends_on='piper_thread_budget'),
         field('max_chars', 'Limite de caracteres por fala', 'number', 'Síntese', min=64,max=10000,step=1),
         field('fallback_voice', 'Voz Piper de reserva', 'select', 'Fallback'),
         field('pocket_enabled', 'Habilitar Pocket TTS', 'switch', 'Pocket TTS'),
         field('pocket_preload', 'Pré-carregar Pocket como motor secundário', 'switch', 'Pocket TTS', depends_on='pocket_enabled'),
         field('pocket_threads', 'Threads de CPU do Pocket', 'number', 'Pocket TTS', min=1,max=4,step=1, depends_on='pocket_enabled'),
         field('pocket_fallback', 'Usar Piper se o Pocket falhar', 'switch', 'Pocket TTS', depends_on='pocket_enabled'),
         field('hf_token', 'Token Hugging Face para Pocket', 'password', 'Pocket TTS', depends_on='pocket_enabled', help='Deixe vazio para manter o token salvo.'),
         field('remote_fallback', 'Permitir fallback para servidor remoto', 'switch', 'Servidor remoto'),
         field('remote_url', 'Endereço do servidor remoto', 'url', 'Servidor remoto', depends_on='remote_fallback'),
         field('remote_token', 'Token do servidor remoto', 'password', 'Servidor remoto', depends_on='remote_fallback', help='Deixe vazio para manter o token salvo.'),
         field('log_level', 'Detalhamento dos logs', 'select', 'Diagnóstico', choices=[('INFO','Normal'),('WARNING','Somente avisos'),('ERROR','Somente erros'),('DEBUG','Depuração')])],
}

def voice_choices(data):
    voices = {'pt_BR-arandu-medium': 'Arandu · nova voz treinada (padrão)', 'pt_BR-faber-medium': 'Faber Medium · PT-BR'}
    directory = data / 'modules/tts/voices'
    for path in sorted(directory.glob('*.onnx')):
        if not path.is_symlink() and VOICE_ID.fullmatch(path.stem) and path.with_suffix('.onnx.json').is_file():
            voices.setdefault(path.stem, path.stem)
    result = list(voices.items())
    pocket = directory / 'pocket'
    for path in sorted(pocket.glob('*.safetensors')):
        if not path.is_symlink() and VOICE_ID.fullmatch(path.stem):
            result.append(('pocket-'+path.stem, 'Pocket · '+path.stem))
    result.append(('pocket-rafael', 'Pocket · Rafael'))
    return result[:100]

def schema(key, options, data):
    result = []
    for original in FIELDS[key]:
        row = dict(original)
        if row['id'] in {'voice', 'fallback_voice'}:
            choices = voice_choices(data)
            row['choices'] = [p for p in choices if row['id'] == 'voice' or not p[0].startswith('pocket-')]
            current = options.get(row['id'])
            if current and current not in {p[0] for p in row['choices']}:
                row['choices'].append((current, current + ' · voz configurada'))
        row['value_type'] = type(options.get(row['id'])).__name__
        if row['type'] == 'password':
            row['configured'] = bool(options.get(row['id']))
        result.append(row)
    return result
