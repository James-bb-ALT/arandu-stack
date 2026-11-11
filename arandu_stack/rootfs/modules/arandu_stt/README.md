# Arandu STT

STT **4.0.1**: FastConformer original/adaptado, Whisper Tiny/Base/Small, Parakeet pt-BR 30s/TAGARELA, Orukeet INT8 e Granite TurboCTC Q4. Instalação individual sob demanda, sem pesos na imagem. Wyoming na porta 10350; gateway da stack na 10212.

Instale somente os módulos desejados. O painel Arandu Stack da mesma loja controla ativação, reinício e opções. Consulte o [guia da stack](https://github.com/jaimevictor/arandu-stack#instalação) para instalação e integração.

Código e licenças originais em rootfs/src. Nenhum peso é distribuído no Git. Versão experimental; execução completa no seu HA precisa de validação. Os testes foram preservados para execução manual, sem rodar inferência durante o build.

Orukeet INT8 é o padrão em instalações novas; seleções existentes são preservadas. O checkpoint continua multilíngue: o decoder sherpa transducer não possui parâmetro suportado para forçar PT-BR. Não há filtro que prometa essa restrição.
