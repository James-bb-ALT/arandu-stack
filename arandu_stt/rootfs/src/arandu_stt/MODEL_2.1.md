# Modelo Arandu STT 2.1

O padrão passa a ser o FastConformer PT-BR adaptado em 13/10/2026, exportado em FP32. A atualização baixa `model.onnx` (456.405.467 bytes) e o tokenizer original da release `v2.1.0`, verificando SHA256 antes de instalar. Não envia áudio ao GitHub.

Somente os 66.177 parâmetros do decoder CTC foram treinados na RTX 3080. Encoder, decoder RNNT e tokenizer foram preservados. Foram concluídas 36 épocas; a época 24 foi escolhida pela validação. Dez exemplos incompatíveis com o comprimento CTC foram excluídos do gradiente; 126 exemplos participaram do treinamento.

No teste reservado de 17 gravações dos sete participantes, executado no Windows com sherpa-onnx CPU, WER original/adaptado foi 20,00%/20,00%; CER foi 6,58%/6,04%. Não houve ganho de WER demonstrado. A exportação FP32 também não preservou todo o ganho do checkpoint NeMo. A versão INT8 adaptada teve WER de 20,74% no Linux, contra 20,00% da FP32. A promoção FP32 foi solicitada explicitamente pelo proprietário após esses resultados; o bloqueio automático de promoção permanece registrado no experimento. A instalação HAOS e a latência no hardware do Home Assistant ainda precisam de validação.

Os arquivos ficam em `/data/models/arandu-ctc-20261013-fp32`. O modelo anterior em `/data/models/int8` permanece disponível. O identificador Wyoming permanece compatível com o pipeline Assist existente.

## Atualização e reversão

1. Atualize o App Arandu STT para 2.1.0 no Home Assistant.
2. Mantenha `model_variant: adapted` e reinicie. Aguarde o download e o log `PRONTO`.
3. Para retornar ao modelo original, selecione `model_variant: original` e reinicie. O motor lexical permanece com as opções existentes.

O modelo FP32 exige mais espaço e pode consumir mais memória que o original INT8. Nenhum inventário ou modelo antigo é apagado.

## Atribuição e licença dos pesos

Modelo base NVIDIA: `nvidia/stt_pt_fastconformer_hybrid_large_pc`, revisão `036da25fa32e3f11c11105448482845950840685`. Adaptação do decoder CTC e exportação ONNX: projeto Arandu, 13/10/2026.

Os pesos derivados são distribuídos sob **Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**: https://creativecommons.org/licenses/by-nc/4.0/. Preserve a atribuição, indique modificações e respeite a restrição de uso não comercial. Esta licença se aplica aos pesos; o código Arandu continua sob MIT.

Gravações, nomes de participantes, transcrições individuais e checkpoint de treinamento não são publicados na release. O manifesto publicado contém apenas proveniência, hashes e métricas agregadas.
