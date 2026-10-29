# Changelog

## 2.2.0 — 2026-10-14

- Novo modelo FP32 como padrão, promovido após avaliação prática e autorização explícita do proprietário.
- Continuação do checkpoint 2.1 com 272 clipes sintéticos e gravações humanas anteriores, dando mais peso a “ventilador” e “Jaime”; 25 épocas e apenas decoder CTC atualizado.
- No teste fixo de 17 gravações, WER passou de 20,00% para 18,52%. O proprietário observou melhoria significativa em suas falas reais; o teste pequeno é mantido como registro técnico, sem substituir essa avaliação.
- Download da release 2.2.0 com novo identificador de cache e SHA256 fixado; modelos antigos e rollback ao original preservados.
- Ferramentas de dataset, treino, geração e comparador organizadas e versionadas; documentação de desenvolvimento e treinamento centralizada.
- CI corrigido para conferir a versão atual e executar testes do aplicativo, pipeline offline e comparador.

## 2.1.0 — 2026-10-14

- Modelo FastConformer adaptado FP32 como padrão, com pesos publicados separadamente na release e SHA256 verificado no download.
- Adaptação apenas do decoder CTC, encoder e tokenizer preservados; 36 épocas, checkpoint escolhido na época 24 pela validação.
- WER no teste Windows permaneceu 20,00%; CER passou de 6,58% para 6,04%. Promoção solicitada pelo proprietário; sem ganho de WER comprovado.
- Opção `model_variant: original` restaura o modelo INT8 anterior. Pesos anteriores e identificador Wyoming preservados.
- Instalação HAOS e consumo no hardware do usuário ainda requerem validação. Consulte `arandu_stt/MODEL_2.1.md`.

## 2.0.0 — 2026-10-06

- Novo corretor contextual modular, independente do FastConformer, ativo por padrão no perfil `full`.
- Vocabulário fechado configurável, proteção de palavras válidas, gramática funcional, números, horários, unidades e compostos.
- Datasets canônicos organizados, deduplicação por origem, manifesto SHA-256 e dataset de nomes fornecido com 569 registros.
- Preservação de grafias válidas de nomes com/sem acento, sem inflar frequências de variantes equivalentes.
- Candidatos indexados, RapidFuzz/Levenshtein, fonética PT-BR, priors limitados, n-gramas e beam search.
- Metadados por palavra/frase, confiança heurística, baixa confiança e erros Wyoming estruturados.
- Contexto HA periódico com andares, aliases, automações e publicação atômica de snapshots.
- Perfis anteriores e rollback preservados; dependências, inferência INT8 e descoberta Wyoming mantidas.
- Testes de integração TCP, regressão, disponibilidade, privacidade; avaliações WER/CER e utilitário opcional de calibração.

Status: implementação local e build Docker verificados; validação de fala real e instalação HAOS pendentes.

## 0.1.1

- Descoberta Wyoming e testes de protocolo do MVP FastConformer INT8.
