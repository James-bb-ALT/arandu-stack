# Arandu — Fase 3 / LLM 2.0

**Pré-release experimental. App 7.0.0, HACS 8.0.0, LLM 2.0.0.**

Planos tipados com parâmetros absolutos/relativos, compostos seguros e contexto curto chegam ao executor HA existente com reconstrução independente das evidências. Recuperação percorre o catálogo autorizado, sem depender exclusivamente do Decision. Conversa geral permanece separada de ações.

Dashboard e AI Lab nativo instalam, selecionam, testam e comparam Tucano Q4_K_M e Qwen3 0.6B Q8_0. Pesos pinados/verificados, inferência offline residente, perfis por modelo, troca confirmada com rollback e compatibilidade legada. Tucano permanece padrão; Qwen3 experimental. Comparação não executa ações. Exports de métricas não contêm falas/IDs residenciais; novos resultados LLM não são persistidos automaticamente no histórico.

Validação executada:

- Build Docker final; 269 testes Python + 200 subtestes, 13 JavaScript, 88 Rust; três testes STT opcionais ignorados.
- Inferência real na imagem final, 37 fixtures técnicas: legado Tucano 12/37; contrato novo Tucano 37/37 e Qwen3 37/37; zero falsas ativações. Ganho do contrato/validação, sem treinamento nem alegação de generalização humana.
- APIs reais de aplicação/reutilização/concorrência/truncamento/rollback; AI Lab com ambos os modelos reais; executor HA simulado, sem ações físicas.

A geração nova usa mais RAM/tempo que as recusas legadas; não há ganho de velocidade comprovado. Sem corpus representativo de 300 falas, teste no i5-6500T ou validação física autenticada no HA. Nenhuma mudança de default neural ou calibração de confiança com fixtures. NLU/Decision/STT/TTS/Wake preservados. Não inclui Fase 4.

Instale o ZIP HACS desta release e atualize o app para usar o contrato novo; instale cada modelo explicitamente pela dashboard. Configurações existentes são preservadas; seleção/perfis ficam em arquivo separado e clientes antigos negociam o contrato legado.

Veja [relatório técnico](LLM-v2.0.0.md), [validação detalhada](LLM-v2.0.0-validation.md) e [métricas](evidence/llm-v2.0.0.json).
