#!/usr/bin/env python3
"""Boots the jar, logs into it, and checks what hostname reached the backend.

Compiling proves nothing here: the patch has to still be doing its job in the
proxy that is about to be published.

Usage: e2e-handshake.py <velocity jar>
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

PROXY_PORT = 25577
BACKEND_PORT = 25599
BACKEND_HOST = "127.0.0.1"
PLAYER_VHOST = "player-typed-this.example.com"
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
    daemon = True

    def __init__(self):
        super().__init__()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((BACKEND_HOST, BACKEND_PORT))
        self.sock.listen(1)
        self.hostname = None
        self.error = None

    def run(self):
        try:
            conn, _ = self.sock.accept()
            with conn, conn.makefile("rb") as f:
                read_varint(f)  # length
                read_varint(f)  # packet id
                read_varint(f)  # protocol
                # anything past a null byte is forwarding data, not the host
                self.hostname = read_string(f).split("\0")[0]
        except Exception as exc:
            self.error = exc


def write_config(directory):
    with open(os.path.join(directory, "forwarding.secret"), "w") as secret:
        secret.write("unused\n")
    # Written out in full: the shipped defaults include example forced hosts
    # pointing at servers that don't exist here, and Velocity won't start.
    with open(os.path.join(directory, "velocity.toml"), "w") as config:
        config.write(
            'config-version = "2.7"\n'
            f'bind = "0.0.0.0:{PROXY_PORT}"\n'
            'motd = "e2e"\n'
            "show-max-players = 20\n"
            "online-mode = false\n"
            'player-info-forwarding-mode = "NONE"\n'
            'forwarding-secret-file = "forwarding.secret"\n'
            "[servers]\n"
            f'lobby = "{BACKEND_HOST}:{BACKEND_PORT}"\n'
            'try = ["lobby"]\n'
            "[forced-hosts]\n"
            "[advanced]\n"
            "compression-threshold = -1\n"  # keeps the login readable
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


def preferred_protocol():
    """Whatever the proxy says it speaks, so this doesn't pin a version."""
    with socket.create_connection(("127.0.0.1", PROXY_PORT), 10) as sock:
        sock.settimeout(10)
        handshake = varint(0x00) + varint(0) + varint(len("status")) + b"status"
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
        handshake = varint(0x00) + varint(protocol)
        handshake += varint(len(PLAYER_VHOST)) + PLAYER_VHOST.encode()
        handshake += struct.pack(">H", PROXY_PORT) + varint(2)
        sock.send(frame(handshake))

        name = b"E2ETester"
        sock.send(frame(varint(0x00) + varint(len(name)) + name + b"\x00" * 16))

        with sock.makefile("rb") as f:
            for _ in range(10):
                body = f.read(read_varint(f))
                if not body:
                    die("the proxy closed the connection during login")
                if body[0] == 0x02:
                    # acknowledging login success is what makes it dial the backend
                    sock.send(frame(varint(0x03)))
                    time.sleep(3)
                    return
                if body[0] == 0x00:
                    die(f"the proxy kicked the test client: {body[1:120]!r}")
        die("never reached login success")


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
            log_in(preferred_protocol())
        finally:
            proxy.terminate()
            try:
                proxy.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proxy.kill()

    if backend.error:
        die(f"the fake backend failed: {backend.error}")
    if backend.hostname is None:
        die("the proxy never connected to the backend")

    print(f"player sent : {PLAYER_VHOST}")
    print(f"backend got : {backend.hostname}")

    if backend.hostname == PLAYER_VHOST:
        die("FAILED: the player's vhost reached the backend. The patch is missing "
            "from this build, or upstream changed how the handshake is built.")
    if backend.hostname != BACKEND_HOST:
        die(f"FAILED: expected {BACKEND_HOST}, got {backend.hostname}")
    print("OK: the backend was told its own address")


if __name__ == "__main__":
    main()
