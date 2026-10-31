# ReSpeaker: memória, cortes de áudio e estado de resposta

## Perfil atualizado para ESPHome 2026.10.1

O YAML informado inclui Bluetooth tracker com `active: false` e proxy Bluetooth
com `active: false`. Isso **não desliga o Bluetooth**: o primeiro mantém varredura
passiva (janela 160 ms / intervalo 320 ms) e o segundo mantém o proxy passivo.
Áudio e voz já pressionavam a RAM interna, conforme o erro `md_reader`/12 e
aproximadamente 4,8 KiB de heap livre. Remover esses componentes, em um satélite
dedicado à voz, elimina uma fonte concreta de consumo e concorrência de rádio.

O novo [perfil de estabilidade](../examples/esphome/respeaker-audio-stability.yaml)
inclui o ajuste de memória e remove Bluetooth tracker/proxy/provisionamento.
Coloca também a tarefa da fonte Sendspin em PSRAM (diferente do hub Sendspin),
desliga economia de energia Wi-Fi e usa buffers HTTP persistentes de 32 KiB.
Expõe RAM interna livre e maior bloco contíguo como sensores de diagnóstico reais.
Isso evita alocações grandes repetidas; não significa que aumentar/reduzir buffer
resolva qualquer falha de rede. Preserva FLAC, 48 kHz, pinos, mixagem e volume.
O script de stop word existente usa `restart`, evitando que uma espera anterior
recuse sua nova execução. Não força LED a idle para esconder uma falha de áudio.

### Aplicar ao aparelho existente

1. Guarde seu YAML atual. Copie `respeaker-audio-memory.yaml` e
   `respeaker-audio-stability.yaml` para a mesma pasta do YAML do aparelho.
2. Use [respeaker-device.example.yaml](../examples/esphome/respeaker-device.example.yaml)
   como modelo: mantém o `!include config/common/respeaker-satellite-base.yaml`
   existente e adiciona o package `arandu_audio` depois dele. Preserve o nome,
   friendly name, Wi-Fi e a chave API atuais, colocando segredos em `secrets.yaml`.
   Não troque a chave do aparelho por uma chave de exemplo.
3. **Remova os blocos raiz** `esp32_ble_tracker` e `bluetooth_proxy` do seu YAML.
   Configuração na raiz prevalece sobre packages e reativaria o que foi removido.
   Não adicione outro bloco com `active: false`. O perfil deixa de oferecer proxy
   Bluetooth e provisionamento BLE; o Wi-Fi já configurado continua sendo usado.
4. Rode `esphome config SEU_ARQUIVO.yaml`, compile no Device Builder e instale.
   Atualização de app/HACS não instala firmware no satélite. Não foi gerado binário
   para o aparelho do usuário e não houve upload/factory reset do XMOS.
5. Teste primeiro um anúncio curto com Bluetooth ausente, depois respostas longas
   e dez repetições. Compare heap interno e maior bloco livre antes/durante TTS;
   procure `md_reader`, `out of memory`, erros HTTP/decoder e transições da pipeline.
   Em seguida teste música simultânea e interrupção. Capture **o log novo após
   instalar o perfil** se ainda houver cortes, incluindo início e fim da resposta.

### Evidência e limites

O perfil foi validado por **ESPHome 2026.10.1** e teve o C++ gerado com
`esphome compile ... --only-generate`, usando o package público
`formatBCE/Respeaker-Lite-ESPHome-integration` no commit
`c01d4c4e802e94d66346bc634d1f3478278647b3` e segredos exclusivamente fictícios.
O merge resultante contém as duas fontes HTTP persistentes, tarefas em PSRAM e
nenhum componente BLE. Não foi compilado/linkado um firmware completo nem testado
o áudio físico. O `config/common/` local do usuário pode ser outra revisão.

A captura reenviada ainda mostra a falha anterior de criação do reader; não
registra o novo sintoma de tocar meio segundo e parar. Portanto, a falta de memória
tem evidência direta; a causa exata do áudio entrecortado após o ajuste ainda não
está comprovada. O perfil é uma correção das pressões identificadas, com validação
no aparelho pendente. Não foi alterado o TTS do servidor sem evidência de falha nele.

## Relatos e implementação conferidos

- A [documentação de Voice Assistant](https://esphome.io/components/voice_assistant/)
  alerta sobre uso de memória/CPU e problemas ao combinar áudio e Bluetooth.
  O [BLE Tracker](https://esphome.io/components/esp32_ble_tracker/) documenta que
  `active: false` apenas desliga solicitações ativas durante a varredura.
- A [PR oficial 18708](https://github.com/esphome/esphome/pull/18708) explica a
  fragmentação causada pela alocação repetida do ring buffer e relata validação
  em ESP32-S3 com dois buffers persistentes de 32 KiB, FLAC e Sendspin concorrente.
  A [opção Audio HTTP](https://esphome.io/components/media_source/audio_http/) foi
  conferida também no código da versão 2026.10.1, não apenas na documentação dev.
- A [issue ReSpeaker Lite 88](https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration/issues/88)
  reúne causas distintas para LED preso, incluindo temporização do LED e fim da
  sessão antes do áudio. Uma luz azul acesa não prova que o decoder esteja tocando.
- As [issues ESPHome 16462](https://github.com/esphome/esphome/issues/16462) e
  [18419](https://github.com/esphome/esphome/issues/18419) identificam o timeout de
  início de TTS de 2 s. A opção `tts_playback_start_timeout`, adicionada pela
  [PR 18707](https://github.com/esphome/esphome/pull/18707), é prevista para
  **2026.11.0**; não foi inserida no YAML compatível com 2026.10.1. Esse timeout
  explica término prematuro em alguns casos, mas não prova a origem dos cortes.
- O [mantenedor do ReSpeaker, issue 92](https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration/issues/92),
  esclarece que I2S usa slots de 32 bits, enquanto os resamplers de fontes de
  16 bits devem continuar em 16 bits. Dobrar essa resolução não melhora o som e
  aumenta memória/trabalho. Esses parâmetros foram preservados.

## Diagnóstico inicial e ajuste de memória

O diagnóstico `Failed to create thread 'md_reader': error 12`, acompanhado de
poucos KiB de heap livre, identifica falha de alocação no firmware do satélite.
A URL de áudio já foi entregue; mudar aliases do NLU não corrige essa etapa.

### Primeiro ajuste de memória (histórico)

O [fragmento de configuração](../examples/esphome/respeaker-audio-memory.yaml)
corresponde aos IDs do YAML fornecido: duas fontes `audio_http`, dois resamplers,
Micro Wake Word e hub Sendspin. Ele adiciona `task_stack_in_psram: true` aos
componentes compatíveis. O mixer já tinha essa opção ligada.

1. Mescle cada propriedade no bloco existente do mesmo componente/ID. O arquivo
   é um fragmento, não uma configuração completa: não substitua seu YAML e não
   duplique as chaves `speaker`, `media_source`, `micro_wake_word` ou `sendspin`.
2. Preserve a declaração de PSRAM, a configuração da placa e os pinos. Não mude
   o FLAC mono a 48 kHz dos anúncios, a saída estéreo ou os buffers neste primeiro
   ajuste. Valide com a mesma versão ESPHome do aparelho; se a opção não existir
   nessa versão/componente externo, não invente um nome alternativo.
3. Valide e compile o YAML completo no ESPHome Device Builder e instale-o no
   aparelho. Atualização do app/HACS não instala firmware no satélite.
4. Repita uma solicitação de voz com os logs abertos. Confirme PSRAM detectada,
   `Decoder Task Stack in PSRAM: YES`, mais heap interno disponível, ausência da
   falha `md_reader` e reprodução efetiva do anúncio. Se ainda falhar, registre o
   heap interno e o maior bloco livre durante a tentativa; não basta memória total.

O flag de `audio_http` move a stack do decoder. A thread de leitura HTTP ainda
requer RAM interna; por isso o ajuste também tira da RAM interna tarefas ativas
de wake word/Sendspin e resampling. Ele é uma correção de alocação orientada pelos
logs, não uma garantia de reprodução sem validar no hardware.

As opções foram conferidas na documentação oficial de
[Audio HTTP Media Source](https://esphome.io/components/media_source/audio_http/),
[Resampler](https://esphome.io/components/speaker/resampler/),
[Sendspin](https://esphome.io/components/media_source/sendspin/) e
[Micro Wake Word](https://esphome.io/components/micro_wake_word/).
O [código de Audio HTTP](https://github.com/esphome/esphome/blob/dev/esphome/components/audio_http/audio_http_media_source.cpp)
separa a stack do reader da stack do decoder.

Esse fragmento não foi compilado nem instalado: falta a configuração completa
do aparelho, incluindo placa, versões e componentes externos. Nenhum endereço,
token de TTS, credencial ou configuração residencial foi incluído no repositório.
