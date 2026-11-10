# v4.0.0 — STT multimodelo e laboratório de voz nativo

**Arandu Assistant 3.0.0 · STT 3.0.0 · HACS 4.0.0.** Nova etapa de release por novas funcionalidades, conforme a classificação de `AGENTS.md`. Versões dos demais motores preservadas.

## Entrega

- FastConformer adaptado/original e Whisper Tiny, Base e Small reais. Whisper CPU INT8, português, duas threads por padrão e sem VAD duplicado.
- Instalação explícita na dashboard, catálogo fechado, staging, hashes, revisão Hugging Face fixada e cache persistente com carga offline. Ambiente STT isolado, sem instalar PyTorch.
- Seleção de produção efetiva, identificação do modelo carregado, aplicação confirmada, reinício somente STT, verificação Wyoming e recuperação da configuração/processo anteriores.
- Aba STT no painel nativo Arandu AI Lab: microfone, upload/player, conversão FFmpeg, comparação sequencial do mesmo áudio, RAW/FINAL, carga/inferência separadas, WER/CER com referência, verificação textual de ação/negação e alvo anotado, JSON/CSV e histórico em RAM.
- Transporte pelo WebSocket autenticado do HA até o `stack_endpoint` configurado. Removida a dependência do serviço legado 8765. Administrador obrigatório, validação de origem/rede, CSRF e limites de áudio, concorrência, tempo e memória.
- Preservados o ContextualResolver, o contrato NLU, o identificador Wyoming legado e as portas 10350/10212. O laboratório STT não executa serviços domésticos.

## Validação executada

- **169 testes Python do app passaram**, com três testes acústicos opt-in ignorados na execução comum; esses três foram executados separadamente e **passaram com pesos reais**.
- **49 testes STT passaram**, incluindo transporte Wyoming, correção contextual e integridade; **1003 subtestes** adicionais nessa suíte.
- **7 testes JavaScript passaram**.
- Chromium: upload e reprodução, Base real, comparação, gravação MediaRecorder, liberação de tracks, exportação JSON/CSV, layout móvel e rejeição de áudio inválido.
- Imagem Linux/Python 3.12: seleção de Base pela dashboard real, aplicação e troca Tiny/Base, comparação de quatro motores com Wyoming atendendo em paralelo, recuperação de candidato ausente e transcrição por 10350/10212.
- Docker build concluído. STT carrega com faster-whisper 1.2.1 e CTranslate2 4.6.0 em ambiente sem PyTorch.
- **84 regressões Rust NLU disponíveis passaram**: biblioteca, contexto e contratos HTTP.
- Tiny, Base, Small e as duas variantes FastConformer carregaram pesos e transcreveram áudio. As três variantes Whisper e FastConformer adaptado também foram verificados em operação offline.

## Medições e limites verificados

Uma frase **sintética** PT-BR de 2,496 s, produzida pelo Microsoft Maria Desktop, em Ryzen 7 5700X/Windows, duas threads: inferência FastConformer adaptado **74 ms**, Tiny **386 ms**, Base **781 ms**, Small **2540 ms**. Carregamentos separados: **1529 / 220 / 381 / 1145 ms**, respectivamente. Uma amostra por motor; não há P95 nem alegação de precisão geral. FastConformer original também transcreveu a amostra, com inferência de 85 ms.

Isso não é benchmark de voz humana nem medição no i5-6500T. Metas de mediana/P95, reconhecimento de negações e nomes reais e microfone físico continuam pendentes de gravações no equipamento de produção. Whisper Small não tem promessa de atingir um segundo.

O HA local respondeu HTTP 401, sem credencial administrativa disponível nesta sessão. Registro do painel e autenticação/encaminhamento têm testes automatizados; instalação e execução autenticada no Home Assistant Core real não foram comprovadas.

A suíte Rust NLU completa foi tentada, mas faltam corpora congelados neste snapshot (`data/mlp/project-authored-synthetic-v1.jsonl` e corpora de `evaluation/ptbr-independent`). Esses casos não foram recriados nem marcados como aprovados. As 84 regressões disponíveis passaram.

O timeout de inferência Wyoming devolve erro após 60 s; uma chamada nativa já iniciada pode terminar em segundo plano, mantendo a trava do motor. Os processos temporários do laboratório são encerrados em timeout ou excesso de RSS. Mais detalhes, instalação, métricas, privacidade, licenças e rollback em [STT-MULTIMODEL.md](https://github.com/James-bb-ALT/arandu-stack/blob/v4.0.0/docs/STT-MULTIMODEL.md).

Pesos FastConformer mantêm licença **CC BY-NC 4.0, não comercial**, com atribuição NVIDIA/Arandu. Whisper é MIT. Nenhum peso ou gravação acompanha esta publicação.
