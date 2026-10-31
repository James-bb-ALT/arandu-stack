# Versionamento e publicação

Antes de publicar mudanças relevantes, atualize as versões dos componentes afetados,
as descrições, o CHANGELOG e as notas da release. Classificação definida pelo usuário:

- Minor: correção, otimização ou melhoria incremental → `x.y.Z` (`0.0.x`).
- Major: mudança grande em comportamento ou arquitetura → `x.Y.0` (`0.x.0`).
- Nova etapa de release: novas features, redesign ou evolução ampla → `X.0.0` (`x.0.0`).

Essa nomenclatura é intencional. Zere os campos à direita do campo incrementado.
Sincronize `arandu_stack/config.yaml`, `APP_VERSION`, o manifest HACS e os
metadados/runtime dos serviços alterados. Atualize README, descrição do app,
CHANGELOG e documentação atual. Preserve versões de componentes não alterados,
dependências e registros históricos. Antes de publicar, confira consistência,
execute os testes afetados e revise o diff. Ao concluir alterações solicitadas,
crie commit e faça push automaticamente, sem aguardar novo pedido. Para atualizações
versionadas de pacote, publique tag e release GitHub com descrição do resultado e
limitações verificadas. Essa autorização é permanente, salvo instrução explícita
para apenas avaliar, não alterar ou não publicar. Inclua apenas alterações da tarefa,
preserve trabalho alheio e não use force-push. Informe versões e link ao concluir;
explique qualquer impedimento real de publicação.
