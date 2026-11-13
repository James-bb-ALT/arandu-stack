"""Real Chromium + Android-viewport Playwright E2E tests for Arandu AI Lab frontend."""
from __future__ import annotations

import base64
import functools
import http.server
import json
import os
import socketserver
import threading
from pathlib import Path

import pytest


CHROME_CANDIDATES = [
    os.environ.get("ARANDU_CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def _find_browser_executable() -> str | None:
    for path in CHROME_CANDIDATES:
        if path and Path(path).is_file():
            return path
    return None


def _start_fixture_server():
    repo = Path(__file__).resolve().parent.parent
    panel_js_path = repo / "custom_components" / "local_nlu" / "ai_lab_panel.js"
    from custom_components.local_nlu.ai_lab import _panel_asset_tag
    tag = _panel_asset_tag()
    wav_b64 = base64.b64encode(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00").decode()

    html = f"""<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body>
<arandu-ai-lab></arandu-ai-lab>
<script>
window.__aranduTestCalls = [];
window.__aranduBlockAutoplay = true;
const origPlay = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function() {{
  if (window.__aranduBlockAutoplay) {{
    const err = new DOMException("play() can only be initiated by a user gesture.", "NotAllowedError");
    return Promise.reject(err);
  }}
  return Promise.resolve();
}};
</script>
<script type="module" src="/local_nlu_ai_lab/panel.js?v={tag}"></script>
<script type="module">
customElements.whenDefined('arandu-ai-lab').then(() => {{
  const el = document.querySelector('arandu-ai-lab');
  el.hass = {{
    callWS: async (msg) => {{
      const payload = msg.payload || {{}};
      window.__aranduTestCalls.push(payload.command);
      if (payload.command === 'readiness') {{
        return {{production_policy: {{nlu: true, decision: false, llm: false}}, decision: {{can_accept: false, reason: 'ok'}}}};
      }}
      if (payload.command === 'catalog') {{
        return {{entities: [{{entity_id: 'light.sala', name: 'Luz da Sala', area_id: 'sala'}}], areas: [{{area_id: 'sala', names: ['Sala']}}]}};
      }}
      if (payload.command === 'tts') {{
        return {{audio: {json.dumps(wav_b64)}, model: 'piper', latency_ms: 25}};
      }}
      if (payload.command === 'run') {{
        return {{
          text: payload.text || 'Qual luz tá ligada?',
          source: 'ha_read_fast_path',
          kind: 'query',
          response_text: 'Estão ligadas: Luz da Sala.',
          operations_validated: false,
          confirmation_required: false,
          sensitive: false,
          total_ms: 4.2,
          catalog: {{entities: [{{entity_id: 'light.sala', name: 'Luz da Sala', area_id: 'sala'}}], areas: [{{area_id: 'sala', names: ['Sala']}}]}},
          stages: {{
            vad: {{status: 'skipped', reason: 'entrada de texto'}},
            stt: {{status: 'skipped', reason: 'entrada de texto'}},
            nlu: {{status: 'skipped', reason: 'Fast path de leitura autorizada'}},
            decision: {{status: 'skipped', reason: 'Fast path de leitura autorizada'}},
            llm: {{status: 'skipped', reason: 'Consulta determinística autorizada (LLM não acionado)'}},
            validation: {{status: 'skipped', reason: 'Consulta somente leitura'}},
            ha: {{status: 'executed', read_only: true, latency_ms: 4.2, output: {{code: 'query_success', response_text: 'Estão ligadas: Luz da Sala.'}}}},
            response: {{status: 'executed', reason: 'Estão ligadas: Luz da Sala.'}},
            tts: {{status: 'skipped', reason: 'não solicitado'}}
          }}
        }};
      }}
      return {{}};
    }}
  }};
}});
</script>
</body>
</html>"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/local_nlu_ai_lab/panel.js":
                body = panel_js_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/local_nlu_ai_lab/stt.js":
                body = b"export function mountStt() { return () => {}; }"
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{port}/", tag


def test_real_chromium_and_android_mobile_layout_and_tts_autoplay():
    playwright_sync = pytest.importorskip("playwright.sync_api")
    browser_exe = _find_browser_executable()
    if not browser_exe:
        pytest.skip("Nenhum binário Chromium/Chrome/Edge disponível neste host")

    httpd, url, expected_tag = _start_fixture_server()
    try:
        with playwright_sync.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=browser_exe, headless=True)
            # 1. Desktop Chromium: autoplay blocked NotAllowedError -> manual Tocar áudio without re-synthesizing
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.goto(url, wait_until="networkidle")
            page.wait_for_function("() => document.querySelector('arandu-ai-lab')?.shadowRoot?.getElementById('run') != null")

            info = page.evaluate("""() => {
              const el = document.querySelector('arandu-ai-lab');
              return {
                version: el.dataset.panelVersion,
                hasPlayAudio: typeof el.playAudio === 'function',
                hasPlayBtn: !!el.shadowRoot.getElementById('playAudio'),
                srcAttr: document.querySelector('script[src*="panel.js"]').getAttribute('src')
              };
            }""")
            assert info["version"] == "11.3.8"
            assert info["hasPlayAudio"] is True
            assert info["hasPlayBtn"] is True
            assert f"?v={expected_tag}" in info["srcAttr"]

            page.evaluate("""() => {
              const el = document.querySelector('arandu-ai-lab');
              el.shadowRoot.getElementById('text').value = 'Qual luz tá ligada?';
              el.shadowRoot.getElementById('autoTts').checked = true;
              el.shadowRoot.getElementById('run').click();
            }""")
            page.wait_for_function("() => document.querySelector('arandu-ai-lab')?.playbackState === 'blocked_by_browser'")

            after_run = page.evaluate("""() => {
              const el = document.querySelector('arandu-ai-lab');
              const sr = el.shadowRoot;
              return {
                ttsState: el.ttsState,
                playbackState: el.playbackState,
                stageTtsStatus: el.last?.stages?.tts?.status,
                ttsStatusText: sr.getElementById('ttsStatus').textContent,
                playBtnDisabled: sr.getElementById('playAudio').disabled,
                ttsCalls: window.__aranduTestCalls.filter(c => c === 'tts').length,
                answerText: sr.getElementById('answer').textContent
              };
            }""")
            assert after_run["ttsState"] == "ready"
            assert after_run["playbackState"] == "blocked_by_browser"
            assert after_run["stageTtsStatus"] == "executed"
            assert after_run["playBtnDisabled"] is False
            assert after_run["ttsCalls"] == 1
            assert "Luz da Sala" in after_run["answerText"]

            # Allow user-initiated play and click Tocar áudio
            page.evaluate("""() => {
              window.__aranduBlockAutoplay = false;
              document.querySelector('arandu-ai-lab').shadowRoot.getElementById('playAudio').click();
            }""")
            page.wait_for_function("() => document.querySelector('arandu-ai-lab')?.playbackState === 'playing'")
            tts_calls_after_manual = page.evaluate("() => window.__aranduTestCalls.filter(c => c === 'tts').length")
            assert tts_calls_after_manual == 1  # No duplicate TTS synthesis!
            context.close()

            # 2. Android/Mobile Chromium viewport (360x800): #stages single-column & readable JSON
            mobile = browser.new_context(viewport={"width": 360, "height": 800}, is_mobile=True, has_touch=True)
            mpage = mobile.new_page()
            mpage.goto(url, wait_until="networkidle")
            mpage.wait_for_function("() => document.querySelector('arandu-ai-lab')?.shadowRoot?.getElementById('run') != null")
            mpage.evaluate("""() => {
              const el = document.querySelector('arandu-ai-lab');
              el.shadowRoot.getElementById('text').value = 'Qual luz tá ligada?';
              el.shadowRoot.getElementById('run').click();
            }""")
            mpage.wait_for_function("() => document.querySelector('arandu-ai-lab')?.shadowRoot?.querySelectorAll('#stages .stage').length >= 5")
            layout_check = mpage.evaluate("""() => {
              const sr = document.querySelector('arandu-ai-lab').shadowRoot;
              const stages = [...sr.querySelectorAll('#stages .stage')];
              stages[0].open = true;
              const r0 = stages[0].getBoundingClientRect();
              const r1 = stages[1].getBoundingClientRect();
              const pre = stages[0].querySelector('pre').getBoundingClientRect();
              const display = getComputedStyle(sr.getElementById('stages')).display;
              return {display, w0: r0.width, w1: r1.width, stacked: r1.top >= r0.bottom - 1, preWidth: pre.width};
            }""")
            assert layout_check["display"] == "grid"
            assert layout_check["w0"] >= 260
            assert layout_check["w1"] >= 260
            assert layout_check["stacked"] is True
            assert layout_check["preWidth"] >= 240
            mobile.close()
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
