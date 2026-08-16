#!/usr/bin/env python3
"""Boots the jar and checks the token reaches the backend on both paths.

The login path and the ping path build their handshake in different classes, and
only one of them used to carry the token. A backend told to accept nothing but
its proxy applies that to status requests as well, so the version without this
kept players out of nothing and left every entry in the server list dead.

Compiling proves neither. This logs in and pings for real.

Usage: e2e-token.py <velocity jar>
"""

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

PROXY_PORT = 25578
BACKEND_PORT = 25598
BACKEND_HOST = "127.0.0.1"
TOKEN = "e2e-token-value"
MARKER = "phantom:" + TOKEN
BOOT_TIMEOUT = 120


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def frame(body):
    return varint(len(body)) + body


def read_varint(f):
    n = shift = 0
    while True:
        b = f.read(1)
        if not b:
            raise EOFError("connection closed")
        b = b[0]
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n
        shift += 7


def read_string(f):
    return f.read(read_varint(f)).decode("utf-8", "replace")


class FakeBackend(threading.Thread):
    """Records the handshake of every connection, keyed by intent."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((BACKEND_HOST, BACKEND_PORT))
        self.sock.listen(8)
        self.seen = {}
        self.error = None

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        try:
            with conn, conn.makefile("rb") as f:
                read_varint(f)  # length
                read_varint(f)  # packet id
                read_varint(f)  # protocol
                host = read_string(f)
                f.read(2)  # port
                intent = read_varint(f)
                self.seen["status" if intent == 1 else "login"] = host
        except Exception as exc:  # noqa: BLE001 - reported by the assertions
            self.error = exc


def write_config(directory):
    with open(os.path.join(directory, "forwarding.secret"), "w") as secret:
        secret.write("e2e-forwarding-secret\n")
    with open(os.path.join(directory, "velocity.toml"), "w") as config:
        config.write(
            'config-version = "2.7"\n'
            f'bind = "0.0.0.0:{PROXY_PORT}"\n'
            'motd = "e2e"\n'
            "show-max-players = 20\n"
            "online-mode = false\n"
            # MODERN is the only mode the token rides in: the others already own
            # the field it goes in.
            'player-info-forwarding-mode = "MODERN"\n'
            'forwarding-secret-file = "forwarding.secret"\n'
            f'phantom-token = "{TOKEN}"\n'
            # What makes the proxy ping the backend at all, which is the second
            # half of what this test is for.
            'ping-passthrough = "all"\n'
            "[servers]\n"
            f'lobby = "{BACKEND_HOST}:{BACKEND_PORT}"\n'
            'try = ["lobby"]\n'
            "[forced-hosts]\n"
            "[advanced]\n"
            "compression-threshold = -1\n"
        )


def die(message, log_path=None):
    if log_path and os.path.exists(log_path):
        with open(log_path) as log:
            message += "\n" + log.read()[-2000:]
    raise SystemExit(message)


def wait_until_listening(process, log_path):
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            die("the proxy exited early:", log_path)
        with open(log_path) as log:
            text = log.read()
        if "Done (" in text:
            return
        if "Your configuration is invalid" in text:
            die("the proxy refused its config:", log_path)
        time.sleep(0.5)
    die("the proxy never finished starting:", log_path)


def ping():
    """A status request, which is what makes the proxy ping the backend."""
    with socket.create_connection(("127.0.0.1", PROXY_PORT), 10) as sock:
        sock.settimeout(15)
        host = "127.0.0.1"
        handshake = varint(0x00) + varint(0) + varint(len(host)) + host.encode()
        handshake += struct.pack(">H", PROXY_PORT) + varint(1)
        sock.send(frame(handshake))
        sock.send(frame(varint(0x00)))
        with sock.makefile("rb") as f:
            read_varint(f)
            read_varint(f)
            return json.loads(read_string(f))["version"]["protocol"]


def log_in(protocol):
    with socket.create_connection(("127.0.0.1", PROXY_PORT), 10) as sock:
        sock.settimeout(15)
        host = "127.0.0.1"
        handshake = varint(0x00) + varint(protocol)
        handshake += varint(len(host)) + host.encode()
        handshake += struct.pack(">H", PROXY_PORT) + varint(2)
        sock.send(frame(handshake))

        name = b"E2EToken"
        sock.send(frame(varint(0x00) + varint(len(name)) + name + b"\x00" * 16))

        with sock.makefile("rb") as f:
            for _ in range(10):
                body = f.read(read_varint(f))
                if not body:
                    die("the proxy closed the connection during login")
                if body[0] == 0x02:
                    sock.send(frame(varint(0x03)))
                    time.sleep(3)
                    return
                if body[0] == 0x00:
                    die(f"the proxy kicked the test client: {body[1:120]!r}")
        die("never reached login success")


def check(backend, path):
    host = backend.seen.get(path)
    if host is None:
        die(f"the proxy never opened a {path} connection to the backend")
    parts = host.split("\0")
    if MARKER not in parts[1:]:
        die(f"the {path} handshake carried no token: {host!r}")
    # The hostname patch has to survive alongside it: the first field is still
    # the backend's own address, not whatever the player typed.
    if parts[0] != BACKEND_HOST:
        die(f"the {path} handshake lost the backend address: {parts[0]!r}")
    print(f"{path:<6} -> {parts[0]} + {MARKER}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <velocity jar>")
    jar = os.path.abspath(sys.argv[1])

    backend = FakeBackend()
    backend.start()

    with tempfile.TemporaryDirectory() as directory:
        write_config(directory)
        log_path = os.path.join(directory, "velocity.log")
        with open(log_path, "w") as log:
            proxy = subprocess.Popen(
                ["java", "-Xms256M", "-Xmx512M", "-jar", jar],
                cwd=directory, stdout=log, stderr=subprocess.STDOUT,
            )
        try:
            wait_until_listening(proxy, log_path)
            protocol = ping()
            log_in(protocol)
            # The ping is answered from a cache on the way in, so give the
            # backend connection behind it a moment to land.
            time.sleep(2)
        finally:
            proxy.terminate()
            try:
                proxy.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proxy.kill()

    if backend.error:
        die(f"the fake backend failed: {backend.error}")
    check(backend, "login")
    check(backend, "status")
    print("both paths carry the token")


if __name__ == "__main__":
    main()
