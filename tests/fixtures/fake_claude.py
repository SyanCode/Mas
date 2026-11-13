#!/usr/bin/env python3
"""A stand-in for `claude setup-token`, as strict as its terminal UI: the code is typed in raw mode, and a Return that
arrives in the same burst as the text counts as part of a paste, not as a validation."""
import os
import sys
import tty

print("\x1b[1mWelcome\x1b[0m. Open this URL to sign in:\r")
print("https://claude.ai/oauth/authorize?code=true&client_id=fake&state=abc\r")
print("Paste code here if prompted > \x1b(B", end="", flush=True)
tty.setraw(0)
typed = ""
while True:
    chunk = os.read(0, 4096).decode()
    if chunk == "\r":
        break
    typed += chunk.replace("\r", "")
    sys.stdout.write("*" * len(chunk))
    sys.stdout.flush()
if typed == "GOOD-CODE#1234":
    sys.stdout.write("\r\nYour token:\r\nsk-ant-oat01-" + "T" * 60 + "\r\n")
else:
    sys.stdout.write("\r\nInvalid code\r\n")
sys.stdout.flush()
