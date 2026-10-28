# Instalar, atualizar e reverter

## Instalação e atualização

1. Use o mesmo repositório de Apps/add-ons e instale/atualize **Arandu STT**, versão 2.0.0. Faça backup das opções atuais antes de atualizar.
2. Mantenha `profile: full`, `lexical_enabled: true` e `closed_vocabulary: true`. O motor novo fica ativo automaticamente.
3. Inicie o App. O log `Resolver` mostra as fontes carregadas; `PRONTO` confirma o serviço Wyoming. Os pesos/modelos existentes são reutilizados.
4. Mantenha a integração Wyoming e o STT atual no pipeline Assist. A atualização não exige criar outra integração.
5. Verifique comandos reais e `/data/metrics.jsonl`. Para avaliar a latência total, use `stop_to_transcript_s`, `inference_s`, `queue_s` e `correction_ms` no hardware HA.

O pacote ZIP de entrega é o código deste mesmo repositório, com seu Dockerfile e metadata de App.
Ele não instala sozinho no Supervisor nem publica uma release GitHub. Para distribuir pelo fluxo atual, disponibilize esta revisão no repositório de Apps já usado.
O build Docker e os 46 testes na imagem Linux/Python 3.12 foram verificados localmente.
A instalação no seu HAOS ainda precisa de validação nesse ambiente.

## Opções novas

| Opção do App | Padrão | Efeito |
|---|---|---|
| `lexical_enabled` | `true` | Motor 2.0 no perfil `full`; `false` usa o resolver antigo |
| `closed_vocabulary` | `true` | Correção obrigatória de OOV; `false` preserva OOV, mas ainda restaura grafia de palavras conhecidas |
| `generic_weight` | `7` | Prior comum às fontes genéricas, intervalo 0–100 |
| `home_assistant_weight` | `10` | Prior HA normalizado, intervalo 0–100 |
| `inventory_refresh_seconds` | `60` | Atualização em background; `0` desativa; recomenda-se pelo menos 15 segundos |

`refresh_inventory: false` reutiliza o cache no startup e evita consultas HA periódicas.
Ainda permite observar o arquivo de inventário a cada intervalo, se houver atualização externa.
Os perfis de diagnóstico antigos continuam disponíveis; não aplicam vocabulário fechado.
`profile: raw` devolve a saída acústica. `profile: legacy_full` executa o pipeline antigo completo.

Para ajustes individuais de coeficientes, beam e dicionário, edite `arandu_stt/config/lexical.json` antes de reconstruir o App.
CLI equivalente: `--lexical-config`, `--generic-weight`, `--ha-weight`, `--inventory-refresh-seconds`, `--no-ha-refresh`,
`--lexical-disabled` e `--open-vocabulary`. Não é necessário editar modelo ou tokens acústicos.
Reinicie o App após mudar configurações/datasets. Mudanças de nomes/áreas/aliases HA são incorporadas no ciclo periódico.

## Desativar e rollback

1. Para voltar ao comportamento anterior dentro do 2.0, defina `lexical_enabled: false` e `profile: full`; reinicie o App.
2. Para remover todo pós-processamento, use `profile: raw`; reinicie.
3. Para reverter a versão inteira, restaure a revisão/imagem 0.1.1 (`ecdc7ae`) e as opções anteriores pelo fluxo atual do App.

Nenhuma migração de banco, alteração de modelo ou transformação destrutiva do inventário foi feita.
O JSON continua tendo suas chaves antigas; campos de andares/automações são aditivos.
Não é necessário apagar `/data/models`, `/data/hub` ou credenciais. Preserve o backup das opções para retornar ao schema antigo.

## Verificação local reproduzível

```bash
python -m pip install numpy rapidfuzz websockets wyoming pyyaml
python -m unittest discover -s arandu_stt/tests -v
python arandu_stt/benchmarks/evaluate.py --repeats 30
python arandu_stt/benchmarks/generate_cases.py
python arandu_stt/benchmarks/evaluate.py --cases arandu_stt/benchmarks/generated_cases.jsonl --output arandu_stt/benchmarks/generated_results.json --repeats 5
```

Os testes não exigem o modelo. Os testes de inferência usam doubles para conferir o contrato Sherpa-ONNX;
o teste Wyoming usa TCP real com saída acústica simulada. Para instalar runtime completo, use o Dockerfile/requirements existentes.
Para validação com gravações reais, produza JSONL com `raw` (saída acústica) e `expected` (referência humana)
e execute `evaluate.py --cases caminho.jsonl --inventory inventario.json`.
Registre latência acústica pelo serviço real; o avaliador textual não mede FastConformer.

Para verificar o runtime acústico instalado, use `benchmarks/acoustic_smoke.py --model-dir /data/models/int8 --output /data/acoustic_smoke.json`.
O padrão usa um segundo de silêncio e não mede precisão de fala. `--audio gravação.wav` aceita WAV mono 16 kHz PCM16.
`--download` utiliza o downloader já existente; pesos ficam fora do pacote de código.
