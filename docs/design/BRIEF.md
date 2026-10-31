# PROMPT PARA CODEX — ARANDU ASSISTANT / REDESIGN VISUAL COMPLETO

Você está trabalhando no repositório local **Arandu Stack / Arandu Assistant**, um app do Home Assistant que reúne Wake, STT, NLU, Decision, LLM e TTS, além da integração HACS e do AI Decision Lab. Sua tarefa é **implementar de ponta a ponta um redesign profissional da interface e da iconografia**, seguindo a identidade visual definida abaixo. Não entregue apenas um mockup: altere o código real, valide a interface e preserve o funcionamento dos módulos.

## 1. Identidade e referência obrigatória

O arquivo de referência é `arandu-icon-original-512.png`, fornecido junto deste prompt. O símbolo escolhido é o ícone **ARANDU** da composição visual anterior: fundo verde-petróleo arredondado; arco externo dourado formando um ciclo/horizonte; sol/círculo superior; arcos internos; pontos alinhados; e um caminho sinuoso dourado na parte inferior.

Também é fornecido `arandu-icon-vetorial.svg`, uma interpretação vetorial simplificada — não um traçado pixel a pixel da imagem original. Use **o PNG original como referência estética prioritária**. O SVG pode ser refinado para usos em que o vetor seja necessário, desde que a silhueta, a leitura em tamanho pequeno e a essência visual sejam preservadas. Não substitua o símbolo por um A estilizado, cérebro, rede neural, circuito, robô ou microfone genérico.

O conceito de marca é: **“Arandu — conhecimento em movimento”**. É uma interpretação artística contemporânea inspirada em ciclos naturais, horizonte, percepção, espiritualidade e sabedoria relacionados ao universo cultural tupi-guarani, não uma representação literal ou comprovadamente tradicional de símbolos religiosos. Respeite essas distinções. Evite grafismos étnicos aleatórios, cocares, penas, apropriações decorativas e afirmações não verificadas sobre tradições sagradas.

Toda a UI deve parecer parte de um único produto: um assistente de IA **local-first**, moderno, confiável, preciso, com identidade própria e visual orgânico controlado. Não produza estética gamer/neon, vidro excessivo, cyberpunk ou dashboard genérico com circuitos.

## 2. Análise do projeto antes de modificar

Comece examinando a árvore real de arquivos, em especial (se ainda existirem com esses nomes):

- `arandu_stack/app/static/index.html` — WebUI do app.
- `arandu_stack/app/static/style.css` — estilos da WebUI.
- `arandu_stack/app/static/app.js` — navegação, estado, módulos, configurações, frases e logs.
- `arandu_stack/app/server.py` e os módulos relevantes — contratos das APIs e estrutura de estados.
- `custom_components/local_nlu/ai_lab_panel.js` — AI Lab com Shadow DOM e WebSocket autenticado via `hass`.
- `custom_components/local_nlu/ai_lab.py` — comandos e dados do laboratório.
- `arandu_stack/INTELLIGENCE.md`, README, testes e rotinas de validação existentes.

Se houve mudanças no repositório desde esta especificação, **o código atual prevalece**. Mapear todos os fluxos antes de refatorar. Não presumir que campos de métricas, estados de produção, consumo de RAM, latência ou health checks existem em determinada API: verificar. Não criar valores de demonstração na tela operacional.

## 3. Tokens e temas

Criar um design system consistente, reutilizável e com escopo apropriado para a WebUI e o AI Lab. Base visual:

| Token | Valor proposto | Uso |
|---|---|---|
| `brand-teal` | `#07575A` | Identidade, navegação e ícones |
| `brand-deep` | `#082C32` | Fundo principal escuro |
| `brand-gold` | `#D5AA73` | Destaques e elementos de marca |
| `brand-sand` | `#F0DDBD` | Texto/ícones de alto destaque no escuro |
| `brand-ivory` | `#F7F4ED` | Fundo principal claro |
| `surface-dark` | `#122D31` | Cards no tema escuro |

Os hexadecimais acima são referências iniciais: ajuste tonalidades secundárias e texto para contraste acessível, mantendo a paleta principal. Cores semânticas de status (sucesso, aviso, erro, informação) devem ser distintas e não depender apenas de cores. A luz dourada não pode ser usada para comunicar “motor saudável” se não houver confirmação real.

Implementar **tema escuro como experiência visual de referência** e tema claro completo. A WebUI pode seguir `prefers-color-scheme`, oferecendo seleção de preferência quando tecnicamente adequado; no AI Lab, priorizar as variáveis nativas do tema do Home Assistant (`--primary-background-color`, `--card-background-color`, `--primary-text-color`, `--secondary-text-color`, `--divider-color`, etc.). Garantir legibilidade nos dois temas.

Tipografia: Inter quando disponível localmente, com fallback robusto de system-ui; dados/JSON/logs com monospace. Escala de espaçamento múltipla de 4 px, cartões com raios aproximados de 12–16 px, contornos discretos, sombras mínimas. Preferir transições de 120–180 ms e respeitar `prefers-reduced-motion`. Não adicionar dependências grandes ou CDN externas.

## 4. Marca e ativos

1. Criar pasta apropriada para ativos da marca, por exemplo `arandu_stack/app/static/assets/brand/`, ajustando à organização efetiva do projeto.
2. Integrar o arquivo oficial `arandu-icon-original-512.png` no cabeçalho, no favicon e nas telas em que a marca é exibida. Gerar tamanhos adequados sem deformar a proporção.
3. Manter também a variante vetorial no projeto para usos em escalas variadas, revisando sua legibilidade em 24, 32 e 64 px. Caso uma versão simplificada seja necessária como favicon, ela deve ser derivada do mesmo desenho.
4. Criar aplicações horizontal (símbolo + “arandu” + indicação discreta “ASSISTANT”), compacta (somente símbolo) e monocromática, sem criar uma nova marca não solicitada.
5. Incluir textos alternativos quando forem imagens sem caráter decorativo.
6. Não confundir a arte com uma certificação de autenticidade cultural ou religiosa.

## 5. Iconografia exclusiva dos seis motores

Desenvolver **seis SVGs próprios**, coerentes entre si e com o logo. Usar arcos, círculos, trajetórias e pontos, com peso visual uniforme; todos precisam funcionar sem rótulos em tamanhos pequenos:

- **Wake**: arcos concêntricos convergindo para um ponto de escuta.
- **STT**: ondas de voz organizadas em sequência de unidades/pontos.
- **NLU**: duas ou mais trajetórias contextuais convergindo em um significado.
- **Decision**: bifurcação fluida com um caminho destacado e nós de escolha.
- **LLM**: constelação de conhecimento com conexões discretas e centro semântico.
- **TTS**: ponto de origem propagando ondas de saída.

Todos devem ser visivelmente distintos, preservando a identidade Arandu. Use ícones utilitários locais e consistentes (Lucide SVG, MDI quando apropriado no HA) para configurações, ligar/desligar, reiniciar, logs, diagnóstico, ajuda, copiar e exportar. **Não usar emoji como ícones de interface.** Ícones devem ter labels acessíveis nos botões.

## 6. Navegação e dashboard principal

Substituir a organização atual “somente cartões” por uma navegação compacta com as seguintes seções, **somente quando houver funcionalidade real**:

- **Visão geral**: saúde operacional e acesso aos recursos.
- **Módulos**: seis motores e suas configurações.
- **Fluxo de inteligência**: explicação/visão dos caminhos reais de execução.
- **Frases personalizadas**: editor existente.
- **AI Lab**: entrada/navegação para o painel próprio do HA, quando disponível; não simular incorporação que não exista.
- **Logs e diagnóstico**: filtro, atualização e download já existentes.
- **Configurações e Ajuda**: somente recursos realmente disponíveis.

A visão geral deve trazer um cabeçalho com a marca, descrição curta do propósito, estado agregado dos processos e cards de cada módulo. Preferir uma hierarquia clara a métricas excessivas. Mostrar números apenas se obtidos de fontes reais e atuais.

Redesenhar o card de motor com ícone específico, título, descrição objetiva, estado, ação principal e ações secundárias. Diferenciar os estados **ativo**, **desativado**, **iniciando**, **reiniciando**, **falha** e **indisponível** quando a API realmente permitir. Não mostrar badges de saúde inventados. Preservar ações existentes de iniciar/parar, reiniciar, editar opções e abrir logs.

**Regra crítica:** manter visual e semanticamente separados:

1. **Processo ligado/desligado** (serviço iniciado, memória e runtime).
2. **Participação na pipeline do Assist** (política de produção da integração HACS).
3. **Prontidão do modelo** (download, carregamento, calibração/serviço disponível).

Ativar o processo Decision/LLM no app não deve ser descrito como habilitar uso real no Assist. Os toggles Decision/LLM da política de produção são independentes. Se a WebUI do app não possui acesso seguro à leitura/edição dessa política, **exibir orientação clara ou link apropriado**, e não criar um controle falso. Não mudar contratos para forçar unificação indevida.

## 7. Fluxo visual — preservar a semântica de execução

Criar um componente visual de pipeline baseado no **caminho sinuoso** do ícone Arandu, com nós/arcos/etapas discretos. Ele deverá mostrar Wake, STT, NLU, Decision, LLM, validação/execução pelo HA e TTS, mas **não como cadeia obrigatória**.

Fluxo real: NLU interpreta primeiro; se resolver, pode encerrar a interpretação. Decision só entra em fallback elegível e habilitado; LLM só entra como último fallback elegível se habilitado. Validação/execução depende das permissões, políticas, confirmação quando necessária e validação pelo HA. Wake/STT/TTS têm seus papéis específicos de áudio e podem estar ausentes em testes textuais.

Estados visuais: **não necessário/dispensado**, **executando**, **executado**, **rejeitado**, **erro técnico**, **aguardando confirmação** quando o backend oferecer essa distinção. Não tratar “ignorado” como falha; não tratar HTTP 200 como proposta aceita; não confundir confiança semântica com similaridade bruta. Mostrar latência por estágio apenas quando medida. Se a API só disponibiliza topologia estática no dashboard, mostrar um **diagrama explicativo explicitamente rotulado**, sem animação que sugira inferência em tempo real.

## 8. AI Decision Lab integrado à identidade

Refatorar a apresentação do `ai_lab_panel.js` sem quebrar Shadow DOM, contratos `hass.callWS`, estado, histórico, gravação, playback, simulação, experimentos, calibração ou exportação. Organizar a experiência em três áreas/abas principais:

**A. Testar:** entrada de texto/gravação, área de origem, modo e modelos, botão de processar, comando original/transcrito, interpretação NLU, hipótese Decision, resposta LLM quando pertinente, ação final proposta, cômodo/dispositivo, status de validação, caminho usado, tempo por etapa e TTS. A proposta/resultado deve aparecer primeiro; dados avançados podem ser recolhidos.

**B. Comparar:** suíte fictícia, exemplos rotulados, execução por lote, comparação de modelos, p50/p95/p99 quando disponíveis, taxas de acerto, rejeições, falsos acionamentos e consumo de memória. Não usar números fictícios para preencher UI. Preservar CSV/JSON e semântica dos benchmarks.

**C. Avançado:** conexões, prontidão, download de modelos, parâmetros, ranking, calibração, JSON, logs/diagnóstico e histórico. Manter entradas sensíveis protegidas e não armazenar tokens onde antes não eram armazenados.

A simulação **dry-run** deve ter identificação persistente, inequívoca e visível. Qualquer opção de execução real permanece desativada por padrão, exige revisão explícita, token temporário e validação do Home Assistant, sem permitir ativação por clique acidental, atalhos de teclado ou controles de demonstração. Não ampliar permissões. Não alterar o comportamento do backend para acomodar um elemento visual.

## 9. Configurações, frases, logs e responsividade

- Preservar esquemas dinâmicos por módulo, selects, switches, campos obrigatórios, validações, dependências e distinção entre **Salvar** e **Salvar e reiniciar**.
- Preservar proteção de tokens: senha não exibida, campo vazio mantém segredo salvo, exclusão explícita; não incluir credenciais em logs ou prints.
- Preservar a edição de até 200 frases e todas as validações atuais. Alertar sobre alterações não salvas ao mudar de aba, se isso puder ser feito sem quebrar a experiência.
- Logs: manter filtro por fonte, limite de linhas, atualização automática a cada cinco segundos, pausa, atualização manual, download, posição de rolagem e mensagem de erro por módulo.
- Breakpoints responsivos aproximados: desktop > 1100 px, tablet 760–1100 px, mobile < 760 px; adaptá-los ao conteúdo real e contexto de painel do HA.
- No mobile, priorizar controles grandes (alvo de toque ~44 px), rolagem sem cortes, formulários de uma coluna e evitar tabelas horizontais obrigatórias quando houver alternativa legível.
- Acessibilidade: teclado, foco visível, semântica HTML, `aria-live` em operações, títulos hierárquicos, contraste WCAG AA para conteúdo e estados não dependentes só de cor.

## 10. Engenharia e compatibilidade

**Não trocar o frontend por React/Vue/Svelte** sem necessidade comprovada. O app atual utiliza HTML/CSS/JS e o AI Lab é um custom element com Shadow DOM: preservar esta arquitetura inicialmente. Prefira CSS tokens e componentes/funções JS reaproveitáveis, sem CSS global que interfira no Home Assistant.

Não quebrar endpoints, CSRF, autenticação, rotas, integração Wyoming, processo Supervisor, descoberta HACS ou APIs de execução. Não iniciar automaticamente benchmarks, downloads, STT, TTS, Decision ou LLM apenas para renderizar a nova interface. Evitar polling adicional desnecessário, carga pesada e pacotes não locais. Manter PT-BR em todas as mensagens.

Se for necessária informação que o backend não fornece, identificar esse requisito e implementar a menor alteração segura com validação/testes, ou omitir o indicador visual. Não inventar métricas. Evitar regressões funcionais em nome da aparência.

## 11. Estratégia de implementação

Trabalhar em fases, concluindo e verificando cada uma:

1. **Auditoria técnica:** inventário de telas, controles, APIs, estados, dependências e testes; registrar riscos reais.
2. **Identidade:** integrar logo, assets, SVGs, tokens, temas, tipografia e componentes base.
3. **WebUI:** redesign completo de cabeçalho, navegação, overview, módulos, diálogos e logs, sem perder ações.
4. **Fluxo visual:** representação correta das rotas de inteligência, com dados reais quando disponíveis.
5. **AI Lab:** navegação interna reorganizada, resultados mais legíveis, dry-run e execução real claramente separados.
6. **Qualidade:** verificar desktop/mobile, acessibilidade, temas, SVGs, testes automatizados e regressão funcional.

Utilizar os comandos/testes existentes do projeto. Se houver `Testar painel.cmd`, testes Python e checks de navegador (`browser_check.py`, `local_check.py`), estudar suas dependências e rodar somente os que forem seguros neste ambiente. Não marcar como aprovado aquilo que não foi executado. Registrar exatamente falhas e limitações.

## 12. Critérios de aceitação

A entrega será considerada concluída quando:

- O **ícone Arandu escolhido** aparece como marca central; nenhuma marca genérica o substitui.
- Tema claro e escuro mantêm consistência visual e contraste, inclusive no AI Lab dentro do HA.
- Existem seis ícones específicos, coerentes e identificáveis em escala reduzida.
- Todos os seis motores mantêm suas ações atuais; configurações, dependências, frases e logs funcionam.
- Processo, política de produção e disponibilidade/calibração do modelo são distinguíveis, sem falsos estados.
- O fluxo visual respeita os caminhos condicionais NLU → Decision → LLM e o papel separado de Wake/STT/TTS.
- Dry-run não dispara ações; execução real segue aprovação explícita e validação existente.
- A aplicação não simula dados de produção, não expõe tokens e não cria downloads/inferências involuntários.
- Mobile, desktop e teclado funcionam; testes existentes permanecem válidos ou são atualizados justificadamente.
- O código permanece organizado, sem reescrita arquitetural desnecessária.

## 13. Entregáveis e relatório final

**Faça as alterações diretamente nos arquivos do projeto.** Ao terminar, entregue:

1. Resumo objetivo do design implementado e das diferenças em relação ao painel anterior.
2. Lista de arquivos criados/modificados, incluindo ícones e tokens.
3. Resultados reais dos testes/checagens com comandos e eventuais limitações.
4. Lista explícita do que permaneceu sem implementação por restrição de API/ambiente.
5. Capturas de tela antes/depois, se o ambiente de execução permitir obtê-las com dados reais ou fixtures claramente identificadas.

Não execute `git push`, crie release ou publique versões sem autorização explícita. Não declare sucesso de integração com um HAOS real se apenas o modo local foi testado. **A prioridade é entregar uma interface visualmente superior sem regredir a inteligência e a segurança do Arandu.**
