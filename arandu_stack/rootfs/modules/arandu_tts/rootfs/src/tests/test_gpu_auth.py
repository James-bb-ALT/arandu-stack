import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from arandu_tts.gpu_server import make_handler


class FakeGPU:
    calls = 0
    def speak(self, text, name):
        self.calls += 1
        assert text == "Olá Arandu"
        assert name == "voz1"
        return b"RIFF-FAKE-TEST"  # basta para testar transporte/autorização


def test_worker_authorization_and_transport():
    fake = FakeGPU()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake, "secret-token-very-long-example"))
    task = threading.Thread(target=server.serve_forever, daemon=True)
    task.start()
    try:
        payload = json.dumps({"text": "Olá Arandu", "voice": "voz1"}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("POST", "/synthesize", body=payload,
                     headers={"Content-Type": "application/json", "Authorization": "Bearer WRONG"})
        reply = conn.getresponse()
        assert reply.status == 401
        reply.read()
        assert fake.calls == 0
        conn.request("POST", "/synthesize", body=payload,
                     headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token-very-long-example"})
        reply = conn.getresponse()
        assert reply.status == 200
        assert reply.read() == b"RIFF-FAKE-TEST"
        assert fake.calls == 1
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        task.join(timeout=3)
