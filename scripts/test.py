#!/usr/bin/env python3
"""Test client - sends fake permission request to bridge daemon."""

import argparse
import json
import socket
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description="Send fake permission request to bridge")
    parser.add_argument("tool", nargs="?", default="Bash", help="Tool name")
    parser.add_argument("hint", nargs="?", default="/buddy:test manual check", help="Hint text")
    args = parser.parse_args()

    sock_path = "/tmp/claude-buddy-bridge.sock"
    req = {
        "kind": "permission_request",
        "session_id": "cmd-test",
        "req_id": f"test-{uuid.uuid4().hex[:8]}",
        "tool_name": args.tool,
        "tool_input": {"command": args.hint} if args.tool == "Bash" else {"hint": args.hint},
        "cwd": "/tmp",
    }

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(60)
        s.connect(sock_path)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode())
        print(f"→ decision from device: {resp.get('decision')}")
    except FileNotFoundError:
        print("✗ bridge daemon not running (no socket). Open a new session or /reload-plugins.")
        sys.exit(1)
    except Exception as e:
        print(f"✗ error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
