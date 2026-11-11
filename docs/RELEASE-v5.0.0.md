# v5.0.0 — oito modelos STT no Arandu

App **4.0.0**, STT **4.0.0**, integração HACS **5.0.0**. Nova etapa de release conforme a classificação do projeto. NLU, Decision, LLM, Wake e TTS conservam suas versões.

O catálogo existente recebe **Parakeet pt-BR 30s**, **Parakeet TAGARELA**, **Orukeet INT8** e **Granite TurboCTC Q4_K_M**, preservando FastConformer original/adaptado e Whisper Tiny/Base/Small. Dashboard e AI Lab listam os oito; cada modelo é instalado individualmente, sem pesos no Git ou na imagem.

Instalação fixa revisões e hashes, mostra download previsto, verifica espaço, usa staging e valida carga antes da promoção. Orukeet verifica o arquivo INT8 completo e extrai somente arquivos permitidos, sem links ou traversal. Parakeet inclui os grandes pesos ONNX externos e compartilha onnx-asr/ONNX Runtime CPU. Granite usa binding e biblioteca transcribe.cpp compilados na mesma revisão, mantendo modelo/sessão persistentes; confere português nas capacidades para recusar conversões antigas.

O AI Lab mantém gravação, upload, player, PCM16 mono 16 kHz, RAW/FINAL, WER/CER, tempos separados e exportação JSON/CSV. Comparação aceita até oito motores, sequencialmente, com erro individual para pesos ausentes. Testes não mudam o Assist nem executam ações. A aplicação exige confirmação, pré-valida o candidato e verifica prontidão Wyoming, com rollback. Portas 10350/10212 e identificador legado preservados.

Validação executada:

- **189 testes Python gerais**, 3 testes acústicos opt-in ignorados nessa execução, mais 19 subtestes.
- **49 testes STT**, mais 1.003 subtestes; **7 testes JavaScript**.
- **3 testes acústicos opt-in** executados separadamente com pesos locais: Orukeet, FastConformer e Whisper Base; operação offline, silêncio e Wyoming.
- Navegador real: oito escolhas, upload/player, inferência Base/Orukeet, comparação, JSON/CSV, MediaRecorder com áudio simulado, liberação do microfone e entrada inválida.
- **84 regressões Rust NLU** disponíveis aprovadas offline. A suíte completa foi tentada e quatro testes de corpus falharam por ausência de `data/mlp/project-authored-synthetic-v1.jsonl`; não foi registrada como aprovada.
- Build Docker final aprovado. Stack Linux sem sobreposição de código: dashboard seleciona Base, instala pelo cache, aplica Orukeet, compara os oito (cinco com pesos e três erros individuais esperados), mantém produção concorrente, verifica Wyoming 10350/10212 e recuperação/troca Tiny/Base.
- Runtime transcribe.cpp **0.4.1** compilado e importado em Linux, ONNX-ASR **0.12.0**, CPU disponível e nenhum Torch no ambiente virtual STT.

Limitações verificadas:

- Apenas **Orukeet** foi baixado nesta tarefa (~487 MB); caches anteriores foram reutilizados. Parakeet 30s, TAGARELA e Granite estão implementados, mas **sem inferência comprovada com seus pesos**.
- O áudio disponível é sintético. Medições de 1/2/4 threads no Ryzen local são smoke tests técnicos, sem ranking de precisão humana, P95 ou promessa de latência no i5-6500T.
- HA respondeu **HTTP 401**; painel/microfone físicos e instalação nesta instância não foram validados. Autenticação e encaminhamento têm cobertura automatizada local.
- Q8_0 não foi acrescentado: não há avaliação de precisão que justifique o download adicional.

[Guia, licenças e instalação](STT-MULTIMODEL.md) · [Registro de validação](STT-EXTENDED-VALIDATION-2026-11-10.json).
