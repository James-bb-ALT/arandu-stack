# Uso

O painel requer o Supervisor do Home Assistant OS/Supervised. Instale os módulos
desejados na mesma loja, abra a WebUI e ative cada um. Desativar para o processo e
define a inicialização como manual; ativar inicia e define como automática.

Configurar exibe as opções existentes, sem tokens e senhas. Salve e reinicie o
módulo. Os valores passam pela validação de schema do Supervisor.

Frases: adicione o texto que você fala e o comando equivalente. Salve. Configure
o endpoint deste painel na integração NLU (porta 8099). Ela consulta as frases em
cada conversa, com limite de tempo e sem cache persistente; falha de conexão
interrompe a conversa configurada para a Stack.

A correspondência é exata após normalização Unicode, caixa e espaços. Não há
regex, execução de Python, serviços arbitrários ou expansão recursiva. As ações
continuam submetidas às permissões e à validação de catálogo do NLU.

O painel tem permissão manager do Supervisor para administrar somente os slugs
Arandu da própria origem. Não expõe porta para a LAN, não oferece proxy genérico
e não envia credenciais aos modelos. Acesso à WebUI depende das permissões do
Ingress do Home Assistant; conceda acesso ao app apenas a usuários autorizados
a administrar os módulos e criar frases.
