# Arandu Assistant

Um único app inclui Wake, STT, NLU, Decision, LLM e TTS. Ative e configure os
módulos internamente pela WebUI. Somente o NLU inicia por padrão.
Não é necessário instalar os motores como apps separados.

App 2.0.0: rastreamento das últimas 50 requisições reais na aba Fluxo de inteligência,
com tempos por etapa, fila, resultados, atualização automática e exportação JSON.
Mantém identidade Arandu, temas claro/escuro e marca sem moldura branca.
HACS 3.0.0 envia as observações sem bloquear a execução. O AI Lab permanece em
Testar, Comparar e Avançado. Os controles e as permissões existentes são mantidos.
O perfil para ReSpeaker fica em `examples/esphome/` no repositório; exige instalação
separada no aparelho. Consulte `ESPHOME-AUDIO.md` para evidência e limitações.

A integração HACS Arandu Assistant é a ponte do agente dentro do HA Core.
Veja o [guia completo](https://github.com/jaimevictor/arandu-stack#instalação).
