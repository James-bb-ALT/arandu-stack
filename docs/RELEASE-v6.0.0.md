# v6.0.0 — conversa geral, Decision rápido e Orukeet padrão

App **5.0.0**, HACS **6.0.0**, Decision **1.0.0**, LLM **1.0.0**, STT **4.0.1**. NLU, Wake, TTS e versões de dependências preservados.

- LLM responde em PT-BR a pedidos gerais, incluindo piadas, perguntas factuais e definições. O caminho de fala não recebe catálogo doméstico/ferramentas nem produz operações. Automação mantém contrato validado e confirmação de hipóteses.
- Decision resolve ação explícita/alvo único sem embeddings e pede confirmação para grafia inequívoca. Corrige platôs do score por mínimo, agrupa consultas, limita cache e pré-carrega E5. Similaridade não vira probabilidade; a nova versão de calibração não reaproveita confiança do algoritmo anterior nem apaga arquivos antigos.
- Orukeet INT8 é padrão em instalações novas. Seleções explícitas e FastConformer legado sem `stt_model` são preservados.
- **Orukeet continua multilíngue:** a API/decoder atuais não oferecem uma trava suportada em PT-BR. Não foi acrescentado um parâmetro ignorado, filtro de tokens ou tradução apresentada como reconhecimento exclusivo em português.

Verificação executada:

- **201 testes Python gerais**, 29 subtestes; três testes acústicos opt-in ignorados nessa suíte e executados separadamente.
- **10 testes dos pacotes Decision/LLM**, **49 testes STT + 1.003 subtestes**, **8 testes JavaScript**.
- **3 testes acústicos reais Orukeet**: offline, silêncio e Wyoming com identificador legado, usando áudio sintético.
- **84 regressões Rust NLU** disponíveis passaram offline. A suíte completa não foi repetida: corpora congelados ausentes neste snapshot, conforme validação anterior.
- Build Docker final aprovado; API da imagem sem sobreposição de código validou E5 real pré-carregado, resolução explícita, confirmação, piada completa, resposta sobre Paris, definição de lâmpada, reutilização do LLM e transporte protegido.
- O Rust v4 real devolveu `no_match` para esses três pedidos gerais com uma lâmpada cadastrada, permitindo o fallback conversacional. A integração HA tem testes de resposta sem ação e de confirmação antes de execução.

Limitações: medições em Ryzen 7 5700X, catálogo pequeno, sem garantia no i5-6500T ou benchmark do áudio completo. A carga/importação mantém custo no início do módulo e é exposta separadamente. HA físico respondeu HTTP 401 e não foi atualizado/testado nesta instância. Tucano 0,5B pode errar informações. Nenhum peso foi publicado.

[Comportamento, causas e limites](INTELLIGENCE-v6.0.0.md) · [Registro de validação](INTELLIGENCE-VALIDATION-2026-11-11.json).
