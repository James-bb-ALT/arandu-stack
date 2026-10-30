# Arandu Assistant

Um único app para Home Assistant com **Wake, STT, NLU, Decision, LLM e TTS**.
Todos os motores estão incluídos na mesma instalação. Você ativa, desativa e
configura cada módulo pela WebUI, sem instalar seis apps separados.

Com **app 0.4.0 + HACS 0.7.0**, escolha quais camadas participam dos comandos reais
em **Configurações → Dispositivos e serviços → Arandu Assistant → Configurar →
Camadas de inteligência**. Decision e LLM são independentes e começam desligados.
Salvar aplica a política às novas requisições, sem reiniciar nem trocar o agente.
Iniciar um processo na WebUI não habilita seu uso na pipeline.
Veja [uso, segurança, diagnóstico e testes das camadas](arandu_stack/INTELLIGENCE.md).

[![Abrir Arandu Assistant no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jaimevictor&repository=arandu-stack&category=integration)
[![Adicionar os apps Arandu ao Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjaimevictor%2Farandu-stack)

## Instalação

1. Adicione este repositório à loja de apps do Home Assistant OS/Supervised.
2. Instale somente **Arandu Assistant** e abra sua WebUI.
3. Ative os módulos que deseja usar. O NLU inicia por padrão; os demais começam
   desativados. Desativar encerra o processo e seus subprocessos, liberando memória.
4. Para usar o agente de conversa e as frases personalizadas, instale a integração
   **Arandu Assistant** pelo HACS e reinicie o HA. Essa integração é a ponte
   necessária dentro do HA Core; o código dela está neste mesmo repositório.
5. Em **Dispositivos e serviços**, configure o **Arandu Assistant descoberto**.
   Os endereços são preenchidos automaticamente pelo app. Se não houver Supervisor,
   use Adicionar integração e informe o hostname e porta **11555** para NLU,
   com painel opcional no mesmo hostname e porta **8099**.
6. Para áudio, configure o **Arandu Wyoming descoberto automaticamente**. STT,
   TTS e Wake ativos compartilham a porta **10212**. Depois escolha os motores
   na pipeline Assist. Se já configurou o TTS, recarregue essa integração após
   ativar o STT para que o HA crie a entidade de reconhecimento.

Exemplo de hostname: `<prefixo-da-loja>-arandu-stack`. O slug `arandu_stack`
foi preservado para atualizar o app anterior sem perder as frases salvas.
NLU e painel usam o mesmo hostname; mudam apenas as portas.
A integração mantém o domínio técnico `local_nlu` para compatibilidade.

## O que está dentro do app

| Módulo interno | Motor | Porta | Inicialização padrão |
| --- | --- | --- | --- |
| NLU | Interpretador contextual Rust | 11555 | Ativado |
| Wake | Wyoming openWakeWord | 10212 conjunta; 10400 direta | Desativado |
| STT | FastConformer PT-BR | 10212 conjunta; 10350 direta | Desativado |
| Decision | Multilingual E5 Small, CPU | 11557, somente loopback | Desativado |
| LLM | Tucano 2 Qwen 0.5B Instruct, GGUF comunitário Q4_K_M | 11558, somente loopback | Desativado |
| TTS | Piper / Pocket | 10212 | Desativado |

Uma imagem, um app, um painel e uma configuração persistente. Os módulos mantêm
processos e ambientes Python isolados; dependências instaladas não significam
modelos carregados. O painel inicia apenas os módulos habilitados.
O pacote fica maior no disco porque já contém os runtimes de todos os motores;
pesos e vozes são baixados separadamente.

## Configuração e frases

Na WebUI você pode ativar, desativar, reiniciar e configurar cada módulo usando
campos, switches e dropdowns. Não há editor JSON. Salve as opções e reinicie o
módulo, ou use **Salvar e reiniciar** quando ele estiver ativo.

| Módulo | Ajustes expostos na UI |
| --- | --- |
| Wake | Alexa, Hey Jarvis, Hey Mycroft, Hey Rhasspy ou Okay Nabu; threshold; trigger level; intervalo entre detecções |
| STT | Modelo adaptado/original, perfil, threads, padding, léxico, vocabulário e sincronização de contexto |
| NLU | Frases personalizadas, limite do comando, duração do contexto e aviso de bateria |
| Decision | Download, threads, confiança calibrada e margem mínima |
| LLM | Download, threads, contexto, limite de tokens e temperatura |
| TTS | Voz, pré-carga, threads, limite da fala, reserva, Pocket e servidor remoto |

A palavra selecionada no Wake é aplicada a todas as conexões do serviço, inclusive
quando um cliente solicita outro nome. Recarregue o motor Wake no Assist após mudar.
Use **app 0.3.5 + integração HACS 0.6.6 ou posteriores** para a descoberta automática
do agente Arandu Assistant, independente da descoberta Wyoming de áudio. Instalar
pelo HACS baixa os arquivos; reinicie o HA para carregar o fluxo de descoberta.
Se já existe uma entrada configurada, ela é mantida e não aparece outro cartão.
HACS 0.6.6 corrige o erro 500 ao abrir Configurar no HA com `probatio`; os
endereços continuam sendo validados ao salvar, sem validadores privados no formulário.
Endpoints vazios são preenchidos
automaticamente pelo app instalado no Supervisor, após verificar a saúde do NLU.
Entradas com nomes padrão antigos passam a se chamar Arandu Assistant; IDs,
políticas e nomes personalizados são preservados.
Tokens ficam ocultos; deixar um campo de senha vazio mantém o valor salvo.

Cadastre até 200 atalhos, por exemplo:

`hora do cinema` → `apague a luz da sala`

A correspondência é exata após normalização Unicode, caixa e espaços. A expansão
ocorre uma única vez, sem regex ou execução de código. A integração preserva
usuário, dispositivo e contexto e continua validando exposição, disponibilidade,
capacidades e permissões antes de executar ações.

As políticas e preferências de dispositivos do NLU continuam nas opções da
integração HA. Decision e LLM permanecem experimentais: estão disponíveis no
AI Lab, sem assumir automaticamente a execução do agente de produção.
O gateway do painel encaminha o catálogo autorizado aos motores internos.

## Logs e diagnóstico

Abra **Logs** no menu do Arandu Assistant. Escolha todos ou filtre por aplicativo,
Wake, STT, NLU, Decision, LLM ou TTS. A página atualiza a cada cinco segundos
enquanto estiver aberta; você pode pausar, atualizar manualmente e baixar os
registros exibidos. O botão **Ver erro** de um módulo abre sua fonte diretamente.

Erros antigos dos módulos continuam disponíveis após atualizar, pois os arquivos
persistentes não são removidos. A página mostra até 100, 300 ou 1.000 linhas finais,
limitadas a 128 KiB por fonte. Tracebacks e códigos de saída são preservados.
Credenciais conhecidas são ocultadas. Se debug_text estiver ligado no STT,
os logs podem conter textos reconhecidos; confira antes de compartilhar.

O app também envia seus próprios eventos e falhas ao registro principal do HA.
Se o aplicativo inteiro não conseguir iniciar, consulte esse registro no HA.
O log do controlador tem rotação de 2 MiB e três cópias anteriores; os arquivos
dos módulos mantêm seu histórico existente.

## Modelos e dados

As vozes Piper **Arandu** e **Faber Medium** acompanham o app, com arquivos ONNX,
configurações e hashes versionados. Arandu, exportada do checkpoint local salvo
na época 145, é o padrão. Use o dropdown do TTS para selecionar Faber Medium.
Não há download para alternar entre essas duas vozes. A voz fixada explicitamente
em um pipeline Assist deve ser alterada também nesse pipeline.

Outros pesos não acompanham o Git. STT, Decision, LLM e TTS começam com
`auto_download: false`; habilite essa opção quando precisar baixar outros modelos.
Wake segue o carregamento de modelos do upstream.
Os pesos ficam no armazenamento persistente do app e não são apagados ao desativar.

Cada módulo tem suas opções e cache próprios. NLU, Decision, LLM e Wake não recebem
credenciais do HA. O STT pode receber a credencial somente para o sincronismo
contextual já existente, quando `refresh_inventory` é habilitado explicitamente.
O app não precisa mais da permissão manager do Supervisor para controlar módulos.

## Atualização da primeira versão

O app antes chamado **Arandu Stack** passa a se chamar **Arandu Assistant**, com o
mesmo slug e porta de painel. As frases são preservadas. A configuração de módulos
passa a ser interna.

Os seis apps antigos, se você chegou a instalá-los, não são desinstalados
automaticamente. Pare-os para evitar processamento duplicado, transfira as opções
desejadas pela WebUI e só depois remova os antigos quando confirmar o novo fluxo.
Na integração, atualize o endpoint NLU nas opções para o hostname do app único;
essa alteração recarrega o cliente automaticamente. STT/TTS/Wake também passam
a usar esse mesmo hostname nas entradas Wyoming.

## Desenvolvimento e validação

Versão experimental **0.4.3**, para HA OS/Supervised amd64.
O código dos motores fica em `arandu_stack/rootfs/modules/`; essa pasta é interna
ao app e não cria apps adicionais na loja. A integração HA fica em
`custom_components/local_nlu/`.

`Testar painel.cmd` executa os testes manuais do controlador. Os testes próprios
dos motores e os benchmarks clicáveis do AI Lab foram preservados. O workflow
de build é exclusivamente manual. Nenhum teste, benchmark ou inferência é iniciado
ao publicar o código. Consulte [o registro de build](BUILD-STATUS.json).

A execução completa em um Home Assistant real ainda precisa de validação.
No Windows com Docker Desktop, `Iniciar painel.cmd` inicia o mesmo app completo
em http://127.0.0.1:8770. Dados ficam em `.local-data`, fora do Git. Pesos E5/Tucano
já presentes no cache do AI Lab são reutilizados por montagem somente leitura,
sem download adicional. Mantenha auto_download desativado para esses pesos locais.
O AI Lab anterior permanece separado em http://127.0.0.1:8766, para comparação.

## Licenças

Código novo do controlador e integração: Apache-2.0. Cada motor e dependência
mantém a licença original; TTS é GPL-3.0-or-later. Pesos E5: MIT. Pesos Tucano:
Apache-2.0; a quantização GGUF é comunitária. As licenças e avisos dos componentes
foram preservados junto ao código-fonte.
