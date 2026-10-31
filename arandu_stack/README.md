# Arandu Assistant

Um único app inclui Wake, STT, NLU, Decision, LLM e TTS. Ative e configure os
módulos internamente pela WebUI. Somente o NLU inicia por padrão.
Não é necessário instalar os motores como apps separados.

App 2.0.1: rastreamento das últimas 50 requisições reais na aba Fluxo de inteligência,
com tempos por etapa, fila, resultados, atualização automática e exportação JSON.
Mantém identidade Arandu, temas claro/escuro e marca sem moldura branca.
HACS 3.0.1 envia as observações sem bloquear a execução e encerra pedidos não
compreendidos no ReSpeaker com “Não entendi.”. O AI Lab permanece em
Testar, Comparar e Avançado. Os controles e as permissões existentes são mantidos.
O perfil para ReSpeaker fica em `examples/esphome/` no repositório; exige instalação
separada no aparelho. Consulte `ESPHOME-AUDIO.md` para evidência e limitações.
O perfil 3.0.1 migra a base antiga do ReSpeaker para o driver I2S oficial,
preservando a conversão do microfone de 48 para 16 kHz.

TTS 0.2.1 entrega áudio completo ao Home Assistant antes da reprodução. Após
atualizar, recarregue a integração Wyoming para renovar as capacidades. Pode
aumentar a espera inicial. Validação física de falas longas ainda pendente.

A integração HACS Arandu Assistant é a ponte do agente dentro do HA Core.
Veja o [guia completo](https://github.com/jaimevictor/arandu-stack#instalação).
