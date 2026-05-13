#!/usr/bin/env python3
"""Status client - queries bridge daemon via Unix socket for BLE status."""

import json
import socket
import sys


def main():
    sock_path = "/tmp/claude-buddy-bridge.sock"

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(sock_path)
        s.sendall((json.dumps({"kind": "ble_status"}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = json.loads(buf.decode()) if buf else {}
        ble = resp.get("ble_connected", None)
        pending = resp.get("pending", 0)
        if ble is None:
            print("  bridge not responding")
        elif ble:
            print(f"  BLE: connected ({pending} pending approval)")
        else:
            print("  BLE: not connected")
    except FileNotFoundError:
        print("  bridge socket not found")
    except Exception as e:
        print(f"  bridge query failed: {e}")


if __name__ == "__main__":
    main()
