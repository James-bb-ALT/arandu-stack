# Segurança

O painel aceita UI pelo proxy Ingress 172.30.32.2 e não publica portas na LAN.
O endpoint interno `/runtime` é somente leitura e limitado à rede do Supervisor.
Operações de escrita exigem token CSRF da sessão do painel. Não há proxy de URL
ou slug arbitrário: os destinos são os seis módulos Arandu da própria loja.

Ingress controla acesso ao painel; trate acesso ao app como permissão administrativa.
A credencial manager do Supervisor fica apenas no controlador e nunca é enviada
ao navegador, respostas de configuração ou processos de inferência.

Atalhos não concedem privilégios. A integração preserva o contexto HA e usa o
mesmo interpretador e fronteira de execução. Falhas ao obter a configuração
Stack interrompem o comando quando o endpoint está configurado.

Não inclua dumps do Home Assistant, áudio, pesos, tokens ou arquivos de opções
em issues e PRs. Relate falhas sem dados pessoais.
