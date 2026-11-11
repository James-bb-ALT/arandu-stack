# Conversa geral, Decision e Orukeet — release v6.0.0

App 5.0.0, HACS 6.0.0, Decision 1.0.0, LLM 1.0.0 e STT 4.0.1. NLU/TTS/Wake e versões de dependências preservados.

## Conversa geral no Assist

NLU continua primeiro. Se o pedido não tiver correspondência e for claramente geral, como “conta uma piada”, “explique gravidade”, “o que é uma lâmpada?” ou “qual é a capital da França?”, o caminho dispensa Decision e pede texto diretamente ao Tucano. O prompt desse modo não contém catálogo doméstico, ferramentas ou gramática de ações. O retorno é envolvido como `intent=conversation`, com alvos vazios, e chega ao Assist/AI Lab em `response_text`. Não cria operações, confirmação ou chamada de serviço doméstico.

Pedidos com sinais de controle doméstico continuam no contrato de automação. O caminho é conservador: comandos misturados, ambiguidade e negação não são promovidos a execução pela conversa. O modo estruturado também pode devolver uma resposta de conversa validada; respostas vazias, excessivas, truncadas ou incompatíveis são recusadas. O HA revalida o usuário após a inferência. Pending/confirmations e bloqueios anteriores continuam protegidos.

O LLM permanece residente entre pedidos. É um modelo local de 0,5B, sem internet; pode errar informações e não deve inventar estados em tempo real. A qualidade de humor não é uma métrica de segurança nem foi avaliada como benchmark.

## Decision: causa e correção

O algoritmo anterior usava `min(cosseno_dispositivo, cosseno_intenção)`. Quando o score do dispositivo era menor que dois scores de intenção, ações diferentes recebiam exatamente a mesma nota. Por exemplo: alvo 0,80, ligar 0,95, desligar 0,90 produziam 0,80/0,80, margem zero. Agora a média gera 0,875/0,85, preservando a diferença. Similaridade continua sem ser tratada como probabilidade.

Há três caminhos:

1. Ação explícita e dispositivo único: resolução determinística, sem embeddings ou confiança numérica inventada. O HA calcula novamente ação, alvo, catálogo, disponibilidade e permissões.
2. Erro de grafia inequívoco, como “acnde ventilador”: hipótese de ação que exige confirmação. Só “sim” autoriza o plano validado; “não” cancela. Não chama LLM para essa hipótese já suficiente para uma pergunta.
3. Restante: E5 residente, descritores em cache, até três consultas num só lote e cache limitado a 128 textos. Hipótese bem separada, mas sem calibração, pode pedir confirmação; execução semântica automática continua exigindo calibração conjunta >=80% e margem suficiente. Empates genuínos continuam ambíguos.

A alteração de score invalida o uso das calibrações anteriores: identificadores `arandu-joint-v2` e `embedding-v2`. Arquivos existentes não são apagados nem convertidos em confiança nova. A calibração rotulada ignora os atalhos determinísticos para medir o algoritmo de embeddings real. Não há ajuste automático do limiar nem calibração com exemplos sintéticos apresentada como validade para a casa do usuário.

Quando pesos estão instalados, o serviço pré-carrega o E5 antes de escutar HTTP e expõe `preload_ms` em seu `/status` interno autenticado. A importação/carga mantém custo no início do módulo; não é escondida dentro do tempo de inferência nem prometida como instantânea. Resoluções explícitas não dependem de carregar pesos.

## Orukeet padrão e idioma

Orukeet INT8 é o padrão de instalações novas, incluindo launcher, configuração e seleção do AI Lab. A atualização conserva seleções explícitas existentes. Instalações legadas sem `stt_model` conservam FastConformer/variante, com migração compatível. Nenhum peso é baixado automaticamente por essa alteração e o modelo de uma instância HA não é trocado remotamente.

**Não foi implementado um bloqueio fictício de idioma.** O checkpoint Orukeet é multilíngue, e a API `OfflineRecognizer.from_transducer` usada nesta versão não aceita `language=pt`. O decoder inicializa o estado com o token blank; não oferece prefixo de idioma configurável. A presença de `<|pt|>` no vocabulário não constitui um controle suportado. Cortar caracteres/tokens ou traduzir depois não faria o reconhecimento ficar exclusivamente PT-BR e poderia prejudicar nomes, comandos e precisão.

Restringir de verdade exigiria outro mecanismo de decodificação validado ou especialização/treinamento e avaliação do modelo; não é uma configuração que possamos ligar com segurança no pacote atual. Foram preservados os pesos INT8 que o usuário aprovou.

Fontes primárias verificadas:

- [Orukeet: arquitetura multilíngue](https://github.com/Oruk-AI/orukeet/blob/24dedf1f6a3fb85bf7db5a2ab236f5a955f17fa2/README.md).
- [API sherpa-onnx 1.13.8](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py).
- [Decoder NeMo transducer](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/offline-transducer-greedy-search-nemo-decoder.cc).

## Validação

Testes automatizados cobrem conversa sem ação, respostas inválidas, confirmação única, ação/alvo revalidados, ausência de alvo único, score sem platô, lote/cache e migração de default. Consulte [registro de validação](INTELLIGENCE-VALIDATION-2026-11-11.json) para resultados executados.

Os testes reais usam E5 e Tucano locais e a mesma revisão de pesos já suportada, sem pesos no Git ou imagem. No Ryzen 7 5700X, foram observados resolução explícita de 0,26 ms, hipótese de grafia de 14 ms e primeira consulta semântica de 13,2 s (12,8 s de carga/importação); repetição em cache de 4,3 ms. São smoke tests de catálogo pequeno, sem garantia no i5-6500T ou medição do áudio fim a fim. A pré-carga da versão final foi verificada separadamente no serviço HTTP.

Tucano respondeu em texto a uma piada completa e à capital da França. A segunda chamada reutilizou o processo, com `load_ms=0`. Não foram executadas ações em uma casa real. Validação física no HA e desempenho no equipamento alvo permanecem separados dos testes locais.

Reprodução: `pytest tests -q`, testes dos pacotes Decision/LLM, testes STT e `node --test tests/test_ai_lab_panel.js`. Os scripts `tests/intelligence_real_check.py` e `tests/intelligence_stack_check.py` são opt-in e exigem pesos instalados; não baixam arquivos.
