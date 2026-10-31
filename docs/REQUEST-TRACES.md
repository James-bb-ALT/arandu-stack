# Rastreamento de requisições do Arandu

## Usar

1. Atualize **app 2.0.0 e HACS 3.0.0**, reinicie o HA e recarregue a WebUI.
2. Confira o endpoint do app na integração Arandu Assistant. O mesmo endereço
   usado para política/frases/fallback recebe a telemetria; nenhuma credencial HA
   é enviada ao app.
3. Na WebUI do app, abra **Fluxo de inteligência**. Faça um pedido pelo Assist
   com o agente Arandu e selecione a requisição no histórico.
4. Para mostrar a fala, habilite **Incluir texto reconhecido nas próximas
   requisições**. Sem isso, a lista mostra horário, ID e resultado. Desmarcar
   remove também os textos que já estavam em RAM.

O histórico começa depois da atualização, não é retroativo. O botão Mais recente
retoma o seguimento automático depois de selecionar um item antigo. Atualização
a cada 2 segundos somente nesta seção, com a página visível; pode ser pausada.
Exportar JSON baixa somente a requisição selecionada. Nenhum botão repete comandos.

## O que é observado

- Preparação: consulta de política/frases no app e expansão quando habilitada.
- Identidade inicial e espera na fila do agente contextual.
- NLU: chamada e validação do contrato v4; retries entram na contagem de tentativas.
- Decision/LLM: execução, aceitação, rejeição, calibração ausente, erro e confirmação.
- Validação HA e execução/consulta: checagens reais e resultado retornado pelo executor.

NLU resolvido evita Decision/LLM; Decision aceito evita LLM. Etapa não alcançada,
desabilitada, não observada, rejeitada e erro técnico são estados distintos.
Esclarecimento de alvo não é apresentado como confirmação de uma ação.
As durações usam relógio monotônico no HA, incluem transporte/validação do estágio
e não são medidas exclusivas de CPU. O total inclui preparação e espera na fila.

Wake, STT e TTS ficam **não observados**: a conversa recebe texto depois do STT e
termina antes da síntese/reprodução da resposta. O perfil não inventa vínculos
entre sessões Wyoming independentes e não usa APIs privadas de traces do HA.
O retorno de uma ação HA não comprova que o equipamento físico mudou de estado.
Para áudio cortando, continue usando o trace da pipeline HA e logs do satélite.

Rotas legadas fora do interpretador contextual podem ter etapas não observadas;
o resultado geral ainda aparece. O caminho contextual v4 é o padrão instrumentado.
O AI Lab e chamadas diretas ao gateway não são conversas de produção e não criam
essas requisições. O recurso não dispara inferência, instalação ou benchmarks.

## Limites e proteção

Até 50 traces em RAM, descartados ao reiniciar. Texto desligado por padrão; a opção
também é volátil. Sem disco/localStorage para traces, áudio, URL TTS, token ou
catálogo de entidades. O texto habilitado passa pela redação de credenciais do app;
o JSON exportado pode conter a fala que você optou por coletar.

Cada conversa tem ContextVar/UUID próprios, inclusive tarefas filhas. Publicação
em segundo plano, até 32 snapshots pendentes (último por ID), até 1,5 s por tentativa,
sem retry infinito. O endpoint local usa o mesmo pinning de DNS/validação local
existente, sem redirects ou token HA. A entrega é de melhor esforço: desconexões
podem perder um trace. Uma requisição em execução sem atualização há mais de dois
minutos mostra estado final desconhecido, não um sucesso presumido.

Ingestão `POST /request-trace`: rede interna do Supervisor, cabeçalho interno
existente e ausência de Origin; limite de 32 KiB, schema fechado, números finitos
e sequências antigas ignoradas. Não é um endpoint de execução. Dados fornecidos
pela rede interna servem somente a diagnóstico, nunca como autorização.

UI: `GET /api/traces`, `POST /api/traces/options`, `POST /api/traces/clear`;
os POST preservam CSRF, e leitura/escrita da UI preservam o boundary ingress.

## Verificação

148 testes Python aprovados, 1 ignorado; 6 JavaScript aprovados. Cobrem schema,
limite e ordem, opt-in/purge/redação, contextos concorrentes, cancelamento,
isolamento de falhas de entrega, ingestão/CSRF/Origin, NLU sem fallbacks,
Decision aceito sem LLM, Decision rejeitado seguido de LLM e contrato NLU inválido.
Browser check valida apresentação dos estados, segundos, controle de coleta e
responsividade. [Captura com fixture](design/screenshots/request-flow-fixture.png).
Sem instalação/validação em HAOS real nesta máquina.
