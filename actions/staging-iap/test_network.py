"""Linux runner smoke test: local TLS server and fake IAP relay, no cloud access."""
import importlib.util
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
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
    requests = []

    def do_GET(self):
        Handler.requests.append((self.headers["Host"], self.path))
        body = self.headers["Host"].encode()
        self.send_response(200)
        # A sized body: newer curl (Ubuntu 26.04) rejects a body that ends at a TCP
        # close without a TLS close_notify, which is all this server sends.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def dial(context, server_hostname):
    """Dial the tunnel the way the caller does: local port, gateway hostname for TLS."""
    with socket.create_connection(("127.0.0.1", tunnel.PORT), timeout=10) as raw, \
            context.wrap_socket(raw, server_hostname=server_hostname) as tls:
        tls.sendall(f"GET /auth/sign-in HTTP/1.1\r\nHost: {tunnel.HOST}\r\nConnection: close\r\n\r\n".encode())
        response = b""
        while chunk := tls.recv(65536):
            response += chunk
    return response


@unittest.skipUnless(os.environ.get("RUN_NETWORK_TESTS") == "1", "Linux Actions runner only")
class NetworkTests(unittest.TestCase):
    def assert_runner_untouched(self, hosts):
        self.assertEqual(Path("/etc/hosts").read_text(), hosts)
        rules = subprocess.run(["sudo", "-n", "iptables", "-t", "nat", "-S", "OUTPUT"],
                               check=True, capture_output=True, text=True).stdout
        for needle in ("127.0.0.2", tunnel.MARKER, str(tunnel.PORT)):
            self.assertNotIn(needle, rules)

    def test_health_check_dials_the_tunnel_directly_and_untrusted_tls_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=frontend.archestra.dev",
                            "-addext", "subjectAltName=DNS:frontend.archestra.dev"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cli = root / "gcloud"
            cli.write_text(RELAY)
            cli.chmod(0o700)
            environment = {"PATH": str(root) + os.pathsep + os.environ["PATH"],
                           "TEST_TLS_PORT": str(server.server_port)}
            bundle = root / "review-action.mjs"
            with urlopen("https://raw.githubusercontent.com/archestra-ai/code-review-action/e3b79886c7b43645bf4fe8816af77700421547a9/dist/index.js", timeout=30) as response:
                bundle.write_bytes(response.read())
            self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(),
                             "0629f51d2f0429d4dfdcc08be90c46e2c6a4b22c1149251849f950529ee93486")
            probe = Path(__file__).with_name("probe-transport.mjs")
            # Fixed public endpoints and a synthetic token only. No live review,
            # workflow identity, cloud credential, or GitHub token is used.
            subprocess.run(["node", str(probe), str(bundle), "before"], check=True, timeout=60)
            hosts = Path("/etc/hosts").read_text()
            trusted_context = ssl.create_default_context(cafile=str(cert))
            trusted_context.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                for trusted in (False, True):
                    state = root / f"state-{trusted}"
                    Handler.requests.clear()
                    with patch.dict(os.environ, {**environment, "CURL_CA_BUNDLE": str(cert) if trusted else "/etc/ssl/certs/ca-certificates.crt"}):
                        try:
                            with patch.object(tunnel.subprocess, "run", wraps=tunnel.subprocess.run) as run:
                                if trusted:
                                    tunnel.start(state)
                                else:
                                    with self.assertRaises(subprocess.CalledProcessError):
                                        tunnel.start(state)
                            # The only command after the tunnel is up is the curl health check,
                            # which dials the local port and keeps the gateway hostname for TLS.
                            commands = [call.args[0] for call in run.call_args_list]
                            self.assertEqual([command for command in commands if command[0] == "curl"],
                                             [commands[0]])
                            self.assertEqual(commands[0][-3:], ("--connect-to", f"{tunnel.HOST}:443:127.0.0.1:{tunnel.PORT}",
                                                                f"https://{tunnel.HOST}/auth/sign-in"))
                            self.assertFalse([command for command in commands if command[0] == "sudo"])
                            if trusted:
                                # The tunnel is up and the runner is unchanged: no hosts entry, no NAT
                                # rule, so no other name can resolve to the tunnel.
                                self.assert_runner_untouched(hosts)
                                self.assertEqual(Handler.requests, [(tunnel.HOST, "/auth/sign-in")])
                                # The probe's "after" phase expects the hostname to be routed on the
                                # runner; nothing routes it now, so GitHub is probed while the tunnel
                                # is up without that staging fetch.
                                subprocess.run(["node", str(probe), str(bundle), "during"], check=True, timeout=60,
                                               env={**os.environ, "NODE_EXTRA_CA_CERTS": str(cert)})
                                head, _, body = dial(trusted_context, tunnel.HOST).partition(b"\r\n\r\n")
                                self.assertIn(b" 200 ", head.split(b"\r\n")[0])
                                self.assertEqual(body, b"frontend.archestra.dev")
                                # The gateway hostname is the TLS servername; the local address is not.
                                with self.assertRaises(ssl.SSLCertVerificationError):
                                    dial(trusted_context, "127.0.0.1")
                        finally:
                            tunnel.stop(state)
                    self.assertFalse((state / "pid").exists())
                    self.assert_runner_untouched(hosts)
                    time.sleep(0.3)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
