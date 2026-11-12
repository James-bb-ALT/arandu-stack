# Arandu Assistant 10.0.0 — Fase 4 experimental

Pedidos independentes deixam de esperar pelo lock global. Conversas e efeitos no
mesmo dispositivo continuam serializados, com autorização, confirmação e estado
relativo revalidados antes de cada efeito. Filas e prazos são limitados; saturação
é explícita, cancelamento é isolado e ações HA não recebem retry automático.

Timers, alarmes e lembretes têm entrega independente da inferência. O scheduler
persiste o claim antes do efeito e não repete ações incertas após reinício. Jobs de
ação antigos sem autoridade original são recusados: recrie esses agendamentos pela
voz após atualizar. Notificações legadas permanecem; nenhuma voz remota é escolhida
automaticamente quando o TTS configurado falha.

O Fluxo mostra filas, saúde/RSS/CPU, percentis e carga/inferência do modelo real.
Exports JSON/CSV omitem fala por padrão. Wake, STT externo e playback sem correlação
pública confiável aparecem como não observados. STT limita capturas; TTS limita
fila e buffer PCM, mantendo síntese integral para Assist. O controle manual de
threads Piper é experimental e desligado: reduziu CPU mas aumentou latência.

Versões: app/HACS **10.0.0**, NLU **0.6.5**, STT **4.0.2**, TTS **0.2.2**.
Decision **3.0.0**, LLM **2.0.0** e Wake preservados. Orukeet INT8 continua padrão
para novas instalações; escolhas existentes, voz e thresholds são mantidos.

O commit Antigravity `549dc61` está incorporado. Seu catálogo/residência LLM é uma
base isolada, sem novas rotas de produção: LFM2.5, LFM3 e Qwen3 1.7B não são
apresentados como modelos homologados. Tucano e Qwen3 0.6B existentes são preservados.

Validação local: suíte raiz com serviços reais, testes de corrida/scheduler,
Rust Windows/Linux, Piper/Orukeet nativos, Wyoming Docker, navegador Chromium,
três Decision CPU e matriz LLM. Zero efeitos físicos nos testes. O benchmark de
orquestração com NLU simulado passou de p95 290,81 para 93,14 ms; não representa
latência áudio-a-áudio. Os ensaios reais de motores não demonstram ganho universal.

Quatro expectativas da suíte Python legada NLU já falham no baseline e continuam
falhando; validadores antigos dependentes de Ruby não rodaram neste Windows.
HAOS, i5-6500T/HD 530, satélites e reprodução audível não foram homologados.
Por isso esta publicação é **pré-release**, sem declarar promoção física completa.

Atualize app e integração HACS juntos e reinicie o Home Assistant. Ajustes avançados
de concorrência na integração recarregam o runtime e preservam jobs persistentes.
Não restaure snapshots antigos de jobs depois de efeitos já disparados: isso
invalidaria a proteção do claim persistido. Rollback de software exige ambos os
componentes compatíveis e revisão dos agendamentos, sem replay de ações incertas.

[Relatório, reprodução, cenários e gargalos](PHASE-4-PERFORMANCE.md).
[Evidência agregada](PHASE-4-EVIDENCE.json).
