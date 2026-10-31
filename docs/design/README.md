# Arandu — conhecimento em movimento

Identidade contemporânea inspirada em ciclos, horizonte e percepção. Não se
afirma que o desenho representa um símbolo tradicional ou religioso.

## Implementação

WebUI com visão geral, módulos, fluxo explicativo, frases, acesso orientado ao
AI Lab, logs e ajuda. Verde-petróleo, areia e dourado, temas claro/escuro/sistema,
tipografia local e seis SVGs próprios. Nenhuma dependência de frontend ou CDN.

O símbolo vetorial é uma interpretação simplificada do PNG fornecido: mantém
sol, arcos, pontos e caminho sinuoso. Não é um traçado automático pixel a pixel.
O PNG original é usado nos cabeçalhos. Há aplicações horizontal, compacta e
monocromática, além dos tamanhos PNG 32/64/192/512. O original 1254 px é preservado.

O AI Lab usa as variáveis de tema do Home Assistant, Shadow DOM e os controles
existentes, reorganizados em Testar, Comparar e Avançado. Simulação permanece
visível; execução real começa desligada e exige proposta revisada/token validado.
As abas não iniciam inferência ou download. Parâmetros, instalação, calibração,
conexões, ranking e histórico ficam em Avançado.

## Fontes e manutenção

- Brief do usuário: [BRIEF.md](BRIEF.md). Diretrizes visuais são especificação;
  contratos e comportamento efetivos do código prevalecem.
- Marca: `arandu_stack/app/static/assets/brand/`. Execute
  `python tools/sync_brand_assets.py` para gerar variantes e sincronizar HACS.
- Tokens WebUI: `arandu_stack/app/static/style.css`.
- Tokens HA e apresentação: `custom_components/local_nlu/ai_lab_panel.js`,
  limitados ao Shadow DOM; não modificam o tema global do HA.

## Evidência visual e funcional

Auditoria preservada: `/api/state` informa somente processo, instalação de runtime,
opções redigidas e esquema; `/api/modules/{id}/{ação}` aplica controles com CSRF;
`/api/phrases` mantém os até 200 atalhos; `/api/logs` mantém fontes, limites e erros.
No HA, `hass.callWS` continua autenticado, com os mesmos comandos e validação.
Gravação, playback, comparação, calibração, exportação e histórico mantêm os IDs
e handlers. Só foram movidos os elementos existentes para as novas seções.

[Antes, WebUI](screenshots/webui-before-fixture.png) ·
[Depois, escuro](screenshots/webui-dark-fixture.png) ·
[Depois, claro](screenshots/webui-light-fixture.png) ·
[Celular](screenshots/webui-mobile-fixture.png) ·
[AI Lab antes](screenshots/lab-before-fixture.png) ·
[AI Lab depois](screenshots/lab-dark-1440-fixture.png).

Todas as capturas usam **FIXTURE_TECNICA**, sem conexão com HA, modelos ou satélite.
`python tests/browser_redesign_check.py` verifica navegação, payloads de salvar e
iniciar, preservação de segredo vazio, três larguras, ambos os temas, ausência de
rolagem horizontal da página, reduced motion e montagem real do custom element.
O teste requer Playwright/Chromium já instalados; não instala navegador/modelos.

Comandos executados nesta entrega:

```text
python -m unittest discover -s tests -q         137 aprovados; 1 ignorado
node --test tests/test_ai_lab_panel.js          6 aprovados
python tests/browser_redesign_check.py         aprovado com Chromium/fixtures
esphome config respeaker-device.example.yaml  válido, ESPHome 2026.10.1
esphome compile ... --only-generate            C++ gerado; não é compilação binária
git diff --check                              aprovado
```

O Python de testes foi o venv local `.venv-ai-lab-313` já disponível. ESPHome foi
instalado em venv temporário isolado; a validação usou a base pública indicada
em `ESPHOME-AUDIO.md`, sem credenciais residenciais. O teste ignorado depende de
um pacote/schema HA ausente no ambiente. Não foi feita auditoria automatizada
com leitor de tela ou declaração de conformidade completa WCAG.

## Arquivos da entrega

- WebUI: `arandu_stack/app/static/index.html`, `style.css`, `app.js`.
- AI Lab: `custom_components/local_nlu/ai_lab_panel.js`, `ai_lab.py`.
- Arte: `arandu_stack/app/static/assets/brand/` (PNG original/derivados,
  `arandu-icon.svg`, `arandu-horizontal.svg`, `arandu-monochrome.svg`,
  `wake.svg`, `stt.svg`, `nlu.svg`, `decision.svg`, `llm.svg`, `tts.svg`).
- Cópias de distribuição geradas: `custom_components/local_nlu/brand/`,
  `arandu_stack/icon.png`; ferramenta `tools/sync_brand_assets.py`.
- Satélite: `examples/esphome/respeaker-audio-memory.yaml`,
  `respeaker-audio-stability.yaml`, `respeaker-device.example.yaml` e
  `arandu_stack/ESPHOME-AUDIO.md`.
- Verificação: `tests/browser_redesign_check.py`, capturas em `screenshots/`.
- Organização: `Arandu.code-workspace`, `docs/WORKSPACE.md`, `docs/design/BRIEF.md`,
  `docs/archive/LOCAL-PACKAGES-LEGACY.md`; ponte externa `../ARANDU-STACK.md`.
- Metadados/documentação: `README.md`, `arandu_stack/README.md`, `config.yaml`,
  `app/server.py`, `CHANGELOG.md`, `INTELLIGENCE.md`, `IMPLEMENTATION-REPORT.md`
  e `custom_components/local_nlu/manifest.json`.

O diagrama do app é explicitamente explicativo, sem métricas inventadas ou animação
de inferência. Estado de processo vem de `/api/state`; política de produção e
prontidão continuam sendo consultadas no HA. Não há leitura segura da política
do HA pelo app, por isso sua edição não foi adicionada à WebUI. Não foram feitos
testes em HAOS real, microfone físico ou reprodução do ReSpeaker nesta máquina.

Reversão: restaurar o commit anterior ao redesign para frontend/metadados e
reinstalar as versões anteriores do app/HACS. Para o satélite, remova o package
`arandu_audio` e restaure seu YAML anterior; não faça factory reset do XMOS.
