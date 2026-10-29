# Arandu STT

STT local em português do Brasil para Home Assistant Assist, usando FastConformer CTC INT8 + motor contextual Arandu 2.0 e protocolo Wyoming.

Consulte [atualização e rollback](UPGRADE_2.0.md), [arquitetura](ARCHITECTURE_2.0.md) e [validação e limitações](REPORT_2.0.md).

## Instalação

1. Adicione este repositório em **Settings > Apps > Install app > ⋮ > Repositories**.
2. Instale **Arandu STT**.
3. Na primeira inicialização, aguarde o download do modelo INT8 (aprox. 131 MB) e a criação do inventário lexical a partir do Home Assistant.
4. O serviço anuncia `wyoming` e deve aparecer como descoberto em **Settings > Devices & services**. Aceite a descoberta.
5. Em **Settings > Voice assistants**, selecione `Arandu STT` como mecanismo de speech-to-text no pipeline desejado.

Se a descoberta automática não aparecer, publique a porta `10350/tcp` nas opções de rede do App e adicione manualmente a integração **Wyoming Protocol** usando o IP do Home Assistant e a porta `10350`.

Nota técnica: o URI de descoberta automática usa o hostname informado pelo Supervisor em `/addons/self/info`. Não use `arandu-stt` fixo; Apps instalados por repositório GitHub recebem prefixo interno do repositório.

## Opções

- `threads`: threads de CPU do sherpa-onnx. Padrão: `2`.
- `padding_ms`: silêncio sintético ao fim do áudio (`0`, `100`, `200`, `300`). Padrão: `0`.
- `profile`: perfil do resolver. Produção: `full`.
- `debug_text`: grava RAW/FINAL nos logs e métricas; desligado por padrão.
- `refresh_inventory`: recria o léxico a partir dos registries do HA ao iniciar. Padrão: ligado.

Nomes, áreas e aliases HA são atualizados em background a cada 60 segundos por padrão.
`lexical_enabled: false` restaura o corretor anterior. `closed_vocabulary: false` preserva palavras desconhecidas.
`generic_weight` e `home_assistant_weight` configuram priors normalizados, inicialmente 7 e 10.
`inventory_refresh_seconds: 0` desativa atualização periódica. Veja opções completas em [UPGRADE_2.0.md](UPGRADE_2.0.md).

## Privacidade

O App consulta localmente os registries de entidades, dispositivos e áreas do seu próprio Home Assistant para montar o léxico. O inventário fica em `/data` dentro do App e não é enviado ao projeto Arandu.

## Modelo e licença

Os pesos não são incluídos neste repositório. Na primeira inicialização, o App baixa a conversão INT8 do checkpoint `nvidia/stt_pt_fastconformer_hybrid_large_pc`.

O checkpoint upstream é **CC BY-NC 4.0**. Portanto, o uso dos pesos possui restrições de uso comercial independentes da licença do código do Arandu.
