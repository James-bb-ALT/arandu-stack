# Arandu Wake — geração sintética Qwen3-TTS

Gerador independente para produzir candidatos de áudio da palavra **Arandu**, negativos próximos e fala cotidiana. **Não treina modelos openWakeWord/microWakeWord, não exporta ONNX/TFLite e não altera Arandu TTS.**

Metas iniciais: 50.000 positivos, 25.000 negativos difíceis e 25.000 negativos gerais. Banco planejado: 200 perfis, divisíveis em 160 train / 20 validation / 20 test. São metas, não quantidades já produzidas nem garantia de 200 identidades vocais perceptualmente distintas. `scripts/status.ps1` mostra produção efetiva.

## Português brasileiro

A versão `arandu-qwen-ptbr-v2` exige `locale: pt-BR`, referências com instruções explícitas de pronúncia brasileira e texto cotidiano brasileiro. O token da API Qwen continua sendo `Portuguese`: ele não distingue Brasil de Portugal. O Base herda o sotaque do áudio de referência; trocar apenas o idioma ou o texto não corrige uma referência com sotaque português europeu.

O lote v1 foi preservado em `archive/ptpt-unverified-v1-*` após relato de sotaque inadequado. Não reutilize essas referências no v2. `accent_validation: pending_listening_review` indica que o sotaque ainda precisa ser ouvido; ASR não comprova sotaque brasileiro. Confira a referência e a prévia antes de retomar a produção em massa. A tarefa agendada foi desativada durante esta correção.

## Operação

No PowerShell, dentro desta pasta:

```powershell
# Preparação reprodutível; não modifica ambientes de outros projetos.
.\scripts\setup.ps1 -DownloadModels
# Testes de código; fixtures de testes não entram no dataset.
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# Síntese real mínima, antes de ligar a geração persistente.
.\.venv\Scripts\python.exe scripts/generate.py --preflight
.\.venv\Scripts\python.exe scripts/verify_preflight.py
# Lançamento independente com confirmação de WAVs novos do processo em background.
.\scripts\start-generation.ps1
```

O preflight gera pelo menos um positivo e um negativo por Qwen, valida PCM e manifesto e termina. A verificação seguinte reabre o estado e reconstrói o manifesto para provar retomada idempotente. O lançamento só emite `READY` depois de observar **novos** WAVs íntegros, um positivo e um negativo, e o processo próprio vivo. `--preflight` não é o processo persistente.

Consulta e controle posteriores são do usuário:

```powershell
.\scripts\status.ps1
.\scripts\stop.ps1
# Aguarde running=false antes de retomar após uma parada.
.\scripts\resume.ps1
```

Parada é cooperativa: termina a síntese atual, preserva WAVs/checkpoints e impede reinício até resume. Não finaliza processos TTS, não apaga dados e não faz alterações de prioridade alheias. Reexecutar start não duplica um worker ativo; também existe lock exclusivo no arquivo de estado.

## Persistência Windows

O lançador tenta registrar uma tarefa própria `AranduWakeDatasetGeneration_<hash-da-pasta>`, em conta atual com privilégios limitados, sem tempo máximo e com início no logon. Se a conta não puder registrar tarefas, cria um hook **do usuário** na pasta Inicializar e usa WMI para criar `pythonw` fora da árvore/job do terminal. A escolha efetiva fica em `state/launcher.json` e no resultado `READY`.

Ambos os modos são independentes de VS Code, terminal e agente. Não dependem de uma janela aberta. A retomada após reboot requer o **logon da conta**; não foi prometida execução antes do login ou após logout. Suspensão, hibernação, desligamento e falta de energia interrompem a execução; ao voltar, usa o estado persistido. O projeto não muda suas políticas de energia nem armazena senha Windows. Um `STOP_REQUESTED` impede a retomada deliberadamente.

## Modelos e convivência

VoiceDesign 1.7B cria uma referência sintética e descrição por perfil; Base 0.6B clona essa referência com transcrição em `Portuguese`. Nunca há dois Qwen grandes carregados simultaneamente pelo gerador. Os snapshots e revisões efetivos estão em `state/models.json`. Snapshots já existentes podem ser reutilizados **somente para leitura**; ausentes vão ao `hf-cache` desta pasta. O worker roda offline depois da preparação. Não usa o serviço OmniVoice como se fosse Qwen.

Esta instalação usa **PyTorch CUDA 12.8** e `resources.device: cuda`. Carrega um modelo por vez em BF16 na RTX 3080. Antes de carregar, exige 5,5 GiB livres para VoiceDesign ou 3,5 GiB para Base, incluindo reserva de 0,75 GiB. Se outro processo ocupar essa memória, fica em `waiting_gpu` e verifica novamente a cada 30 segundos; não interrompe o outro projeto nem inicia síntese na CPU. ASR continua na CPU. `scripts/setup.ps1` instala CUDA por padrão; `-CpuOnly` permite uma instalação CPU deliberada, que também exige mudar `resources.device` para `cpu`.

Não há promessa de arquivos/hora: concorrência por VRAM pode impedir o início de uma síntese. Quando as metas forem atingidas, encerra normalmente. Falta de disco, referências inconsistentes ou falhas repetidas geram estado `blocked`, com logs e dados preservados. Perfis que esgotam as tentativas por qualidade/semelhança são registrados e pulados; referências adulteradas continuam bloqueando. A tarefa possui até três reinícios por falha.

## Vozes e separação

Com `resources.prepare_voice_bank_first: true`, a execução contínua prepara primeiro as referências dos 200 perfis planejados, mantendo VoiceDesign carregado. Depois valida as referências com Base, sem voltar ao VoiceDesign nessa passagem. Só então gera os clipes com Base carregado. A retomada reaproveita os perfis e as tentativas já salvas; referências corrompidas bloqueiam, tentativas esgotadas são registradas e puladas. Durante o preparo, a contagem de clipes fica parada: acompanhe `phase`, `prepared_reference_profiles` e `validated_voice_profiles` em `scripts/status.ps1`. O preflight curto mantém seu comportamento anterior.

Descrições variam gênero vocal, idade adulta/idosa, timbre, textura, articulação e sotaques brasileiros de seis grupos regionais. Não citam pessoas reais. Seed sozinha não define identidade. Cada referência passa por QC e pelo speaker encoder do Base. Na política `uncalibrated_cosine_review_v2`, cosine acima de 0,94 exige revisão, mas não rejeita automaticamente: esse encoder/limiar não foi calibrado como classificador de identidade. Referências com SHA idêntico ou embedding exatamente idêntico continuam rejeitadas. Similaridade, características acústicas e `identity_distinctness: not_verified` ficam registradas. Isso **não certifica diversidade humana nem isolamento perceptual entre splits**. Todo áudio de perfil sinalizado fica em `review`, inclusive negativos gerais, e é excluído da exportação padrão.

O filtro anterior rejeitou 227 referências apenas por cosine entre 0,948 e 0,990. `scripts/reassess_diversity.py` preservou os metadados originais e um backup SQLite em `archive/diversity-policy-v1-*`, liberou essas tentativas para reavaliação e reposicionou o cursor sem apagar WAVs. Referências e embeddings já salvos são reutilizados, com verificação de checksum, evitando centenas de novas sínteses.

Cada `speaker_id` pertence a um split fixo antes da síntese. Perfis train não são reajustados com dados validation/test. Held-out pode ser comparado aos perfis anteriores para impedir duplicação da referência, sem alterar vozes de treinamento. Não altere seed, vozes, política de split, modelos ou parâmetros de geração depois de começar: o projeto bloqueia misturas. Metas podem ser ampliadas. Para 500+ perfis, crie uma nova versão/pasta antes de iniciar; a expansão foi testada como planejamento, não gerada antecipadamente.

## Áudio, limites e pronúncia

WAVs canônicos: PCM16 mono 16 kHz, resampling polifásico, margens curtas, amplitude controlada. WAVs limpos na taxa nativa também são preservados em `data/native`. Alterações leves de velocidade/ganho são aumentação, **não novas identidades**. Há detecção de clipping, silêncio, duração implausível, não-finitos, loops de áudio e duplicatas antes de padding/velocidade. Erros de truncamento possíveis viram revisão. Atividade de energia sozinha não prova voz.

Texto positivo permanece Arandu; instruções pedem A-RAN-DU, sílaba final DU. ASR independente tiny/CPU é corroborativo. Não reconhecer Arandu não descarta automaticamente o áudio: fica `review` com confiança apropriada. Mesmo uma transcrição correta não certifica os fonemas. Negativos cujo ASR contém Arandu são rejeitados. Todos os positivos e negativos difíceis exigem revisão explícita para exportação padrão.

**Arando merece atenção:** em muitas variantes do português brasileiro, o `o` final reduz para /u/; a distinção também depende de acento e contexto, não apenas da letra final. Negativos próximos são candidatos de revisão, nunca exemplos garantidamente distintos por inspeção do texto.

A distribuição programada dos positivos é 70% isolados, 20% com contexto e 10% interrogação/exclamação. No modo contexto, wake word e comando são **duas sínteses da mesma referência**, unidas com pausa curta; isso permite fronteira conhecida sem fingir alinhamento forçado. O nativo preserva o contexto; o clipe canônico contém somente a wake word. Esses exemplos não equivalem a uma única fala humana contínua. Metadados mostram método e fronteiras; início/fim de atividade por energia não são timestamps fonéticos perfeitos.

## Estado e recuperação

`state/dataset.sqlite` é a autoridade transacional; `data/manifests/samples.jsonl` recebe registros incrementais. Antes do commit, ambos os WAVs são escritos atomicamente e um sidecar registra seus checksums. Recomeçar recupera sidecars íntegros e reconstrói o JSONL por streaming. Arquivos órfãos são preservados, sem sobrescrever áudio potencialmente válido. Clipes com erro têm diagnóstico em `data/rejected`; suas tentativas não contam para a meta. Os WAVs de referências rejeitadas permanecem no respectivo perfil.

O cursor e as seeds persistem; o gerador alterna classes na proporção 50/25/25 e troca de voz a cada oito clipes, sem terminar em poucas centenas. A síntese permanece em lote unitário para limitar memória. Falhas acústicas têm tentativas limitadas e são substituídas por novos jobs; erros consecutivos persistentes bloqueiam de forma segura.

O manifesto registra ID, classe, texto canônico/pretendido, voz, sotaque, modelo/revisão, seed, parâmetros, dtype/device, taxa nativa/canônica, duração, split, caminhos/checksums, processamento, contexto e validação. Reprodutibilidade por seed é melhor esforço: não há promessa de bit-identical entre hardware/versões. `scripts/build_manifest.py` pode reconstruir manualmente somente quando o worker estiver parado.

## Preparação futura

```powershell
.\.venv\Scripts\python.exe scripts/prepare_dataset.py --engine openwakeword --out exports/oww-v1
.\.venv\Scripts\python.exe scripts/prepare_dataset.py --engine microwakeword --out exports/mww-v1
```

Os exportadores produzem **índices de áudio**, não features nem modelos. Por padrão excluem `review`; `--include-review` é um opt-in consciente para candidatos ainda não revisados. No futuro, openWakeWord extrairá seus embeddings; microWakeWord utilizará seu microfrontend/espectrogramas. Os formatos de features não são intercambiáveis. Não foi iniciado treinamento.

Ruído, RIR, banda aproximada de microfone e codec µ-law têm transformações lazy em `wakegen.audio_validation.augment`, mantendo os limpos. Registre fontes legais de TV, música, conversa, ventilador e respostas ao impulso em uma cópia do registro `external_assets.example.json`; valide referências/checksums/licenças declaradas com `python scripts/register_external.py meu-registro.json`. Não foram fabricadas gravações de ambientes. Não grave inúmeras cópias aumentadas para inflar horas. O catálogo aceita WAVs negativos e referências a arrays de embeddings openWakeWord, sem confundi-los com features microWakeWord. Catálogos externos aparecem na descrição dos índices exportados; incorporação e compatibilidade nativa exigirão o adaptador de treinamento futuro. Os negativos sintéticos não substituem esses dados reais.

Referências primárias consultadas: [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS/tree/022e286b98fbec7e1e916cb940cdf532cd9f488e), [openWakeWord](https://github.com/dscripka/openWakeWord), [microWakeWord](https://github.com/kahrendt/microWakeWord), [fontes negativas microWakeWord](https://github.com/kahrendt/microWakeWord/blob/main/documentation/data_sources.md), [modelos ESPHome](https://github.com/esphome/micro-wake-word-models). Formatos comuns de áudio são reutilizados; os pré-processamentos de treinamento continuam separados. Desempenho real exigirá vozes humanas inéditas e horas de negativos domésticos representativos.
