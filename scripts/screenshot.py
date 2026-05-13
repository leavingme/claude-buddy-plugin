#!/usr/bin/env python3
"""Screenshot client - sends screenshot request to bridge daemon via Unix socket."""

import argparse
import base64
import json
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description="Take screenshot via bridge daemon")
    parser.add_argument("-o", "--output", default="/tmp/buddy_screenshot.jpg", help="Output path")
    args = parser.parse_args()

    sock_path = "/tmp/claude-buddy-bridge.sock"

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect(sock_path)
        sock.sendall((json.dumps({"kind": "screenshot"}) + "\n").encode())

        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        sock.close()

        line = buf.split(b"\n")[0]
        resp = json.loads(line)
        if resp.get("ok"):
            jpg = base64.b64decode(resp["data"])
            with open(args.output, "wb") as f:
                f.write(jpg)
            print(f"Screenshot saved: {args.output} ({len(jpg)} bytes)")
        else:
            print(f"Error: {resp.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
