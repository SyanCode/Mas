#!/usr/bin/env python3
"""A stand-in for `claude setup-token`: prints a link, waits for a code, prints a token."""
import sys

print("\x1b[1mWelcome\x1b[0m. Open this URL to sign in:\r")
print("https://claude.ai/oauth/authorize?code=true&client_id=fake&state=abc\r", flush=True)
code = sys.stdin.readline().strip()
if code == "GOOD-CODE#1234":
    print("Your token:\r")
    print("sk-ant-oat01-" + "T" * 60 + "\r", flush=True)
else:
    print("Invalid code\r", flush=True)
