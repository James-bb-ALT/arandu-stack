# ReSpeaker: memória, cortes de áudio e estado de resposta

## Mitigação específica: HACS 3.0.1

O usuário confirmou que os cortes persistiram apesar do firmware e buffering.
Para esse caso, pedidos não compreendidos no ReSpeaker passam a responder apenas
**“Não entendi.”** e encerrar. O contexto pendente desse diálogo é removido;
um “sim” posterior não retoma a hipótese abandonada. Sucesso, consultas e
confirmações de ações permanecem iguais; outros dispositivos não são afetados.
O perfil usa o cadastro do dispositivo de origem (modelo/nome/hostname ReSpeaker),
sem classificar todo ESPHome/Raspberry como limitado. Atualize a integração HACS
para 3.0.1 e reinicie o HA. Não requer outra OTA ou atualização do app.
Essa é uma mitigação da resposta de incompreensão, não um conserto geral do áudio.

## Falas longas: entrega completa pelo TTS 0.2.1

Após a OTA 3.0.1, a escuta real melhorou no começo da frase, mas ainda travou
na contagem final. Portanto, a migração I2S não encerrou a investigação.
Arquivos FLAC completos e HTTP fragmentado reproduziram até o fim nos testes;
uma resposta recém-sintetizada não encerrou durante a captura de 45 s.

Teste adicional: arquivo FLAC completo de **35 s** reproduzido a 10% de volume;
ANNOUNCING em 2,915 s, decoder concluído/IDLE em 37,754 s. O usuário confirmou
que esse áudio tocou bem. Isso comprova reprodução longa com arquivo completo,
mas não substitui testar a pipeline depois de atualizar o servidor TTS.

O TTS 0.2.1 deixa de anunciar `supports_synthesize_streaming`. No Home Assistant,
isso seleciona o caminho que acumula o áudio inteiro antes de servi-lo ao player.
Assim, o satélite não depende da geração das próximas frases durante a reprodução.
O protocolo incremental continua aceito para clientes existentes. O custo é maior
espera inicial e armazenamento temporário do áudio no servidor, não no ESP32.

Atualize o app e recarregue a integração Wyoming do Arandu TTS para renovar as
capacidades descobertas. Apenas reinstalar o firmware não aplica essa mudança.
Validação automatizada: 29 testes do TTS aprovados. A hipótese de esvaziamento
do buffer ainda precisa ser confirmada com uma fala longa no aparelho atualizado.
Referência do caminho de buffering: `homeassistant/components/tts/__init__.py`,
ramo `not engine_instance.async_supports_streaming_input()`; integração
`homeassistant/components/wyoming/tts.py`, `async_get_tts_audio`.

### Instalação e teste da pipeline em 31/10/2026

O HA confirmou app **2.0.1 em execução** após atualização, e a integração Wyoming
Arandu confirmou a recarga. Uma fala inédita de várias frases, contagem de um a dez
e encerramento foi enviada por `tts.speak` usando `tts.arandu_tts` para o Media Player
físico do ReSpeaker, a 10% de volume. HTTP 200 em 13,632 s da captura;
ANNOUNCING em 13,685 s; leitura HTTP concluída em 40,629 s; decoder concluído
e IDLE em 40,758 s. Não houve erro no trecho capturado. Aproximadamente 27 s
até concluir, sem o bloqueio observado na captura anterior de 45 s.
Esta medição comprova término do fluxo; a confirmação de escuta do usuário
se refere ao teste anterior com arquivo completo de 35 s.

O package foi persistido em `config/common/respeaker-legacy-i2s-fix.yaml`,
o include do YAML do aparelho foi conferido e a cópia da raiz preservada como
`.yaml.bak`, evitando um dispositivo extra. Configuração com o novo caminho
validada pelo ESPHome 2026.10.1. Nenhuma nova OTA foi necessária para esse ajuste.

## Base antiga: driver I2S e conversão do microfone (3.0.1)

A base **2026.6.0** instalada no aparelho foi conferida diretamente. Ela importa
`formatBCE/esphome@respeaker_microphone` para **todo** o componente `i2s_audio`,
incluindo a saída de áudio. Portanto, compilar com ESPHome 2026.10.1 não garante
que o speaker use o driver dessa versão. O fork consultado termina no commit
`04a9f40154d2720bd90745f025f6a6deb50f5681`, de 02/10/2026.

O [package para a base antiga](../examples/esphome/respeaker-legacy-i2s-fix.yaml)
seleciona por último o I2S **oficial 2026.10.1** e adiciona um microfone decimator
fixado por commit. O driver oficial contém recuperação de perda de eventos,
escrita DMA parcial e dessincronização que difere do fork. Essa é uma correção
concreta da mistura de versões, instalada por OTA e conferida no aparelho.
Não significa que toda falha de áudio tenha essa única causa.

O adaptador do microfone é necessário: o fork antigo anunciava 16 kHz apesar
de ler o barramento XMOS a 48 kHz e descartar dois de cada três frames. Com
I2S oficial, `i2s_mics` continua fisicamente a 48 kHz e `arandu_voice_mic` faz
explicitamente a conversão para 16 kHz. Voice Assistant e Micro Wake Word usam
esse adaptador, preservando seus canais e ganho. Pinos, slots de 32 bits,
clock do XMOS, volume, stop word e firmware XMOS não são alterados.

1. Preserve uma cópia do YAML e da base atuais. Copie também
   `respeaker-legacy-i2s-fix.yaml` para `config/common/`, evitando que o package
   apareça como um aparelho offline extra no ESPHome Builder.
2. **Somente na base antiga com esse fork**, adicione após os outros packages:
   `arandu_i2s: !include config/common/respeaker-legacy-i2s-fix.yaml`, como no exemplo.
   Uma base moderna que já usa I2S oficial e decimator não deve receber esse
   adaptador novamente. Não coloque o fork I2S novamente na raiz do YAML.
3. Use o compilador **ESPHome 2026.10.1**, valide e compile antes de instalar.
   O driver foi fixado nessa versão; uma atualização futura do ESPHome requer
   rever o pin. Confira o log: a última origem de `i2s_audio` deve ser
   `esphome/esphome@2026.10.1`. Instale o firmware e teste em volume baixo.

### O que foi observado no aparelho

Com o perfil de memória instalado, o heap interno em repouso chegou a
aproximadamente 104 KB, com maior bloco de 86 KB; Wi-Fi em torno de −40 dBm.
Isso supera a falha anterior de alocação com 4,8 KB, mas não comprova qualidade
de áudio. Um trecho FLAC mono de 48 kHz, PCM de 16 bits e duração 9,09 s foi
baixado e decodificado completamente no PC. Reprodução direta no satélite,
inclusive com HTTP chunked, apresentou ciclos que terminaram em cerca de 9 s.
Esses estados e tempos não substituem escuta ou gravação do alto-falante.

Durante a investigação o usuário desconectou e reconectou o aparelho. Os
timeouts de API coincidentes **não são prova de deadlock**. Também não reutilize
uma URL do proxy como se fosse um arquivo permanente: o proxy pode encerrar a
transcodificação anterior quando a mesma URL é requisitada novamente. Não faça
download de uma URL enquanto o satélite estiver reproduzindo essa mesma URL.

Fontes verificadas: [speaker do fork](https://github.com/formatBCE/esphome/blob/04a9f40154d2720bd90745f025f6a6deb50f5681/esphome/components/i2s_audio/speaker/i2s_audio_speaker_standard.cpp),
[speaker oficial 2026.10.1](https://github.com/esphome/esphome/blob/2026.10.1/esphome/components/i2s_audio/speaker/i2s_audio_speaker_standard.cpp),
[base moderna com decimator](https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration/blob/c01d4c4e802e94d66346bc634d1f3478278647b3/packages/respeaker-lite/hardware.yaml)
e [proxy ESPHome no HA](https://github.com/home-assistant/core/blob/dev/homeassistant/components/esphome/ffmpeg_proxy.py).

### Validação da migração em 31/10/2026

Configuração, compilação e link aprovados com ESPHome 2026.10.1. O arquivo C++
do driver incluído no build teve hash idêntico ao da versão oficial. O binário
ocupou aproximadamente 2,9 MB, cabendo na partição de 3,75 MiB. A atualização OTA
foi concluída e a API do aparelho confirmou `arandu.respeaker_lite` **3.0.1**.
O diagnóstico confirmou decimator em 16 kHz e cerca de 127 KB de heap interno
livre em repouso. Três reproduções de FLAC (uma HTTP chunked) terminaram em IDLE
em cerca de 9 s cada, para áudio de 9,09 s, com volume em 10%. Essa verificação
comprova conclusão do fluxo; a ausência de cortes audíveis depende de escuta.

## Perfil de memória anterior (histórico da entrega 2.0.0)

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
