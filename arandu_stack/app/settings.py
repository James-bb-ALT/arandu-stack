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
FIELDS = {
 'wake': [field('wake_word', 'Palavra de ativação', 'select', choices=WAKE_WORDS, help='Esta seleção vale para todas as conexões do serviço interno. Recarregue o motor Wake no Assist após mudar.'),
          field('threshold', 'Sensibilidade (threshold)', 'number', min=.01, max=.99, step=.01, help='Valores menores detectam com mais facilidade, mas podem gerar falsos positivos.'),
          field('trigger_level', 'Ativações consecutivas (trigger level)', 'number', min=1, max=20, step=1),
          field('refractory_seconds', 'Intervalo entre detecções (segundos)', 'number', min=0, max=60, step=.1)],
 'nlu': [field('custom_phrases_enabled', 'Usar frases personalizadas', 'switch', help='Os ajustes do NLU exigem a integração HACS atualizada e o endpoint do painel configurado.'),
         field('max_input_chars', 'Limite de caracteres por comando', 'number', min=64, max=512, step=1),
         field('session_ttl', 'Duração do contexto da conversa (segundos)', 'number', min=1, max=600, step=1),
         field('low_battery_threshold', 'Aviso de bateria baixa (%)', 'number', min=0, max=100, step=1)],
 'decision': [DOWNLOAD, THREADS,
              field('threshold', 'Confiança mínima calibrada', 'number', 'Decisão', min=.5, max=1, step=.01, help='Similaridade não é confiança. Sem calibração válida o modelo continua se abstendo.'),
              field('min_margin', 'Margem mínima entre candidatos', 'number', 'Decisão', min=.001, max=1, step=.001)],
 'llm': [DOWNLOAD, THREADS,
         field('context_size', 'Janela de contexto (tokens)', 'select', 'Geração', choices=[(2048,'2.048'),(4096,'4.096'),(8192,'8.192')]),
         field('max_tokens', 'Limite da resposta (tokens)', 'number', 'Geração', min=64, max=512, step=1),
         field('temperature', 'Temperatura', 'number', 'Geração', min=0, max=1, step=.05, help='Zero produz respostas mais previsíveis.')],
 'stt': [field('model_variant', 'Modelo de reconhecimento', 'select', choices=[('adapted','Arandu adaptado'),('original','FastConformer original')]),
         DOWNLOAD, THREADS,
         field('padding_ms', 'Áudio adicional nas bordas', 'select', 'Áudio', choices=[(0,'Sem padding'),(100,'100 ms'),(200,'200 ms'),(300,'300 ms')]),
         field('profile', 'Perfil de correção', 'select', 'Reconhecimento', choices=[('full','Completo'),('raw','Sem correção'),('normalize_only','Somente normalização'),('acoustic_only','Somente acústico'),('fuzzy_only','Somente aproximação'),('inventory_only','Somente inventário'),('legacy_full','Completo legado')]),
         field('lexical_enabled', 'Usar correção lexical', 'switch', 'Reconhecimento'),
         field('closed_vocabulary', 'Limitar ao vocabulário conhecido', 'switch', 'Reconhecimento'),
         field('generic_weight', 'Peso do vocabulário geral', 'number', 'Reconhecimento', min=0,max=100,step=1),
         field('home_assistant_weight', 'Peso dos nomes do Home Assistant', 'number', 'Reconhecimento', min=0,max=100,step=1),
         field('refresh_inventory', 'Sincronizar nomes e áreas do HA', 'switch', 'Contexto', help='Permite ao STT consultar o inventário pela API do Home Assistant.'),
         field('inventory_refresh_seconds', 'Intervalo de atualização do inventário (segundos)', 'number', 'Contexto', min=0,max=86400,step=1, help='Zero desativa a atualização periódica.'),
         field('debug_text', 'Registrar textos reconhecidos para diagnóstico', 'switch', 'Diagnóstico', help='Os textos ficam nos logs do módulo; mantenha desligado no uso normal.')],
 'tts': [field('voice', 'Voz principal', 'select', help='Arandu e Faber Medium já acompanham o app. Define o padrão do serviço; uma voz escolhida explicitamente no Assist tem prioridade.'),
         DOWNLOAD,
         field('preload', 'Pré-carregar voz ao iniciar', 'switch', 'Desempenho'),
         field('cpu_threads', 'Threads de CPU do Piper', 'number', 'Desempenho', min=1,max=4,step=1),
         field('max_chars', 'Limite de caracteres por fala', 'number', 'Síntese', min=64,max=10000,step=1),
         field('fallback_voice', 'Voz Piper de reserva', 'select', 'Fallback'),
         field('pocket_enabled', 'Habilitar Pocket TTS', 'switch', 'Pocket TTS'),
         field('pocket_preload', 'Pré-carregar Pocket', 'switch', 'Pocket TTS', depends_on='pocket_enabled'),
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
