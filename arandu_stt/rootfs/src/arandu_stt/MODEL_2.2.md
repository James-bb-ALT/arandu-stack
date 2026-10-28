# Modelo padrão Arandu STT 2.2

O padrão da versão 2.2.0 é o modelo FP32 que o proprietário avaliou no
comparador em 14/10/2026. Após constatar melhoria significativa com suas falas
reais, ele solicitou explicitamente a publicação deste artefato como padrão.
O arquivo publicado é idêntico ao modelo avaliado, com SHA-256
`68fff73a46efa49914df12fa74e83e3e36803ab80a8fbea86b6f7c8684e3c112`.

## Atualização

1. Atualize o App Arandu STT para **2.2.0** no Home Assistant.
2. Mantenha `model_variant: adapted` e reinicie.
3. Aguarde o download verificado de `model.onnx` (456.405.387 bytes) e o log `PRONTO`.

Os novos arquivos ficam em `/data/models/arandu-ctc-20261014-continued-fp32`.
O cache da versão 2.1 e o original INT8 permanecem disponíveis. Para usar o
original, selecione `model_variant: original` e reinicie. A identidade Wyoming
usada pelo pipeline Assist foi preservada.

## Treinamento e evidência

O treinamento continuou a partir do checkpoint que originou a versão 2.1,
com 272 clipes sintéticos e as gravações humanas anteriores. O decoder CTC
recebeu 25 épocas, com LR 0,00003 e maior repetição de frases com “ventilador”
ou “Jaime”. Participaram do gradiente 377 exemplos únicos; 31 exemplos tiveram
alinhamento CTC inviável. Encoder, RNNT e tokenizador permaneceram iguais.

O artefato promovido corresponde à última época, `final_epoch.nemo`, e à sua
exportação FP32. O checkpoint escolhido pela validação interna NeMo ficou na
época zero. Esse critério automático não foi alterado retroativamente; a
promoção deste artefato foi decidida pelo proprietário após avaliá-lo na prática.

No runtime usado pelo Arandu, o teste separado de 17 gravações passou de
27 para 25 erros de palavras: WER 20,00% para 18,52%. CER passou de 6,04% para
6,31%. Na validação FP32, WER passou de 17,05% para 16,28%. Esses conjuntos
pequenos registram uma amostra específica; a avaliação de fala real do
proprietário orientou a promoção. A instalação HAOS ainda requer verificação
no dispositivo do usuário.

## Proveniência e licença

Base: `nvidia/stt_pt_fastconformer_hybrid_large_pc`, revisão
`036da25fa32e3f11c11105448482845950840685`. Adaptações do decoder CTC: Arandu,
13 e 14/10/2026. Os pesos derivados são distribuídos sob **CC BY-NC 4.0**;
o código continua sob MIT. A release inclui hashes, proveniência e métricas
agregadas, sem gravações, transcrições individuais ou checkpoint NeMo.
