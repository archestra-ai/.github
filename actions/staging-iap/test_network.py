"""Linux runner smoke test: local TLS server and fake IAP relay, no cloud access."""
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location("tunnel", Path(__file__).with_name("tunnel.py"))
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)

RELAY = '''#!/usr/bin/env python3
import os, select, socket, threading
def relay(client):
    with client, socket.create_connection(("127.0.0.1", int(os.environ["TEST_TLS_PORT"]))) as upstream:
        while True:
            for source in select.select([client, upstream], [], [])[0]:
                data = source.recv(65536)
                if not data: return
                (upstream if source is client else client).sendall(data)
with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 18443))
    listener.listen()
    while True:
        threading.Thread(target=relay, args=(listener.accept()[0],), daemon=True).start()
'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.headers["Host"].encode())

    def log_message(self, *_):
        pass


@unittest.skipUnless(os.environ.get("RUN_NETWORK_TESTS") == "1", "Linux Actions runner only")
class NetworkTests(unittest.TestCase):
    def test_normal_https_uses_the_tunnel_and_untrusted_tls_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=frontend.archestra.dev",
                            "-addext", "subjectAltName=DNS:frontend.archestra.dev"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cli = root / "gcloud"
            cli.write_text(RELAY)
            cli.chmod(0o700)
            environment = {"PATH": str(root) + os.pathsep + os.environ["PATH"],
                           "TEST_TLS_PORT": str(server.server_port), "NO_PROXY": "frontend.archestra.dev"}
            try:
                for trusted in (False, True):
                    state = root / f"state-{trusted}"
                    with patch.dict(os.environ, {**environment, "CURL_CA_BUNDLE": str(cert) if trusted else "/etc/ssl/certs/ca-certificates.crt"}):
                        try:
                            if trusted:
                                tunnel.start(state)
                                with urlopen("https://frontend.archestra.dev/auth/sign-in",
                                             context=ssl.create_default_context(cafile=str(cert)), timeout=10) as response:
                                    self.assertEqual(response.read(), b"frontend.archestra.dev")
                            else:
                                with self.assertRaises(subprocess.CalledProcessError):
                                    tunnel.start(state)
                        finally:
                            tunnel.stop(state)
                    self.assertNotIn(tunnel.MARKER, Path("/etc/hosts").read_text())
                    rule = subprocess.run(["sudo", "iptables", "-t", "nat", "-C", "OUTPUT", *tunnel.RULE],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.assertNotEqual(rule.returncode, 0)
                    time.sleep(0.3)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
