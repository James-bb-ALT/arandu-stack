# ReSpeaker: URL de TTS recebida, mas sem áudio

O diagnóstico `Failed to create thread 'md_reader': error 12`, acompanhado de
poucos KiB de heap livre, identifica falha de alocação no firmware do satélite.
A URL de áudio já foi entregue; mudar aliases do NLU não corrige essa etapa.

## Ajuste preparado

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
