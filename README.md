# Arandu Stack

Assistente local e modular em português para Home Assistant. Um repositório reúne
Wake, STT, NLU, Decision, LLM, TTS e o painel de administração.

[Adicionar à loja do Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjaimevictor%2Farandu-stack)

## Instalação

1. No Home Assistant OS/Supervised, adicione este repositório à loja de apps.
2. Instale **Arandu Stack** e os módulos desejados. Abra a WebUI do painel.
3. Adicione este repositório ao HACS como **Integração**, instale **Arandu NLU** e
   reinicie o Home Assistant. Alternativamente, copie `custom_components/local_nlu`
   para `/config/custom_components/local_nlu` e reinicie.
4. Adicione a integração **ARANDU NLU**. Informe o endpoint interno do módulo NLU.
   Nas opções da integração, informe também `stack_endpoint`, o endereço do painel.
5. Adicione os módulos de áudio pela integração **Wyoming** e configure o pipeline Assist.

Os nomes internos usam o prefixo da loja. Por exemplo, se o painel mostrar
`abcd1234_ptbr_nlu`, o endpoint NLU será `http://abcd1234-ptbr-nlu:11555` e o
endpoint Stack será `http://abcd1234-arandu-stack:8099`. Use o prefixo real
mostrado no seu painel, não o exemplo. No Wyoming informe o hostname sem `http://`
e a porta do módulo.

| Módulo | Implementação | Porta interna | Opcional |
| --- | --- | --- | --- |
| Arandu Stack | WebUI de administração via Ingress | 8099 | Para controle central/frases |
| Wake | Wyoming openWakeWord + ferramentas de satélites Arandu | 10400 | Sim |
| STT | FastConformer INT8 + contexto lexical | 10350 | Sim |
| NLU | Interpretador contextual Rust + integração HA | 11555 | Para agente Arandu |
| Decision | Multilingual E5 Small, CPU | 11557 | Sim |
| LLM | Tucano 2 Qwen 0.5B Instruct, GGUF comunitário Q4_K_M | 11558 | Sim |
| TTS | Piper / Pocket | 10212 | Sim |

Instalar um módulo não torna os outros obrigatórios. Você pode usar STT/TTS/Wake
de outros fornecedores com o agente NLU. Decision e LLM continuam experimentais:
não assumem automaticamente a execução de comandos do agente de produção.

## WebUI

- **Módulos:** estado consultado no Supervisor; ativar, desativar e reiniciar
  cada app individualmente. Ativar/desativar também altera a inicialização automática.
- **Configurar:** opções existentes em JSON, validadas pelo Supervisor. Salve e
  reinicie o módulo. Tokens e senhas atuais são ocultados e preservados.
- **Frases personalizadas:** cadastro, edição, exclusão e ativação de até 200
  atalhos. Exemplo: `hora do cinema` → `apague a luz da sala`.

As frases usam correspondência exata após normalização Unicode, caixa e espaços.
Não são regex nem treinamento. A expansão ocorre uma vez, antes do NLU, preservando
usuário, dispositivo e contexto da conversa. A integração continua validando
exposição, disponibilidade, capacidades e permissões reais antes de executar.

O endpoint Stack é opcional. Quando configurado, indisponibilidade do painel ou
NLU desativado interrompe a conversa com uma resposta explícita. Quando vazio,
o NLU funciona de forma independente, sem os atalhos do painel.

## Modelos e privacidade

Nenhum peso é incluído no Git. Decision e LLM começam com `auto_download: false`;
ative essa opção e reinicie para baixar a revisão fixada. STT/TTS/Wake podem baixar
modelos/vozes na primeira inicialização, conforme as opções de cada app.
O build do LLM compila llama.cpp em CPU, sem otimizações específicas da máquina.

O painel usa Ingress sem porta publicada para a LAN e recebe credencial manager
do Supervisor somente para controle dos slugs Arandu da própria loja. Limite
o acesso ao painel a administradores. Os motores Decision/LLM não recebem
credenciais do HA e propõem resultados sem executar serviços.

Não são enviados caches, pesos, áudio, inventário residencial, tokens ou ambientes
virtuais. O código dos módulos fica em `<app>/rootfs/src`; código de geração de
dataset Wake fica em `arandu_wake/rootfs/dataset_generation`. `rootfs` impede que
o Supervisor interprete configurações internas do código-fonte como novos apps.
Os diretórios originais do workspace não são alterados.

## Estado desta versão

Versão inicial experimental, focada em HA OS/Supervised **amd64**. O NLU também
mantém suporte a aarch64; os demais módulos precisam de validação antes dessa arquitetura.
O repositório contém os builds locais de cada app e a integração instalável via HACS.
Instalação e fluxo de voz completo em um HA real ainda precisam de validação.
Nenhum teste, benchmark ou inferência foi executado nesta entrega por solicitação
do usuário; builds e consultas de status não carregam modelos.

## Desenvolvimento e testes manuais

`tests/test_control.py` cobre contratos do painel. `Testar painel.cmd` executa os
testes quando você clicar. Os testes próprios de cada módulo foram preservados.
O AI Lab do NLU também mantém seus datasets sintéticos e botões de benchmark.
O workflow de build é exclusivamente manual, sem executar testes ao publicar.

Para abrir apenas a WebUI neste PC: `Iniciar painel.cmd`. O modo local mostra
explicitamente que o Supervisor não está conectado; não simula módulos ativos.
O AI Lab que já estava em execução continua separado em http://127.0.0.1:8766.

## Licenças

Código novo do painel e integração: Apache-2.0. Cada módulo e dependência mantém
sua licença original: NLU/Decision/LLM Apache-2.0; TTS GPL-3.0-or-later;
modelos E5 MIT e Tucano Apache-2.0. Quantização Tucano é comunitária, não oficial.
Consulte os arquivos LICENSE e THIRD_PARTY_NOTICES dentro dos módulos para os
demais componentes. Não há redistribuição de pesos por este repositório.

Referências de implementação: [apps e configuração do HA](https://contributors.home-assistant.io/apps/reference/configuration/),
[API do Supervisor](https://contributors.home-assistant.io/apps/supervisor-api/apps/).
