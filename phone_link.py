"""Starts the app and creates a private HTTPS link for your phone.

Run with:  .venv/bin/python phone_link.py
Add --mock to test with fake scans and no API calls.
"""

import io
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

import qrcode

PORT = 8765
ROOT = Path(__file__).resolve().parent
LINK_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def print_qr(link: str) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(link)
    output = io.StringIO()
    qr.print_ascii(out=output, invert=True)
    print(output.getvalue())


def main() -> None:
    if not shutil.which("cloudflared"):
        sys.exit("cloudflared is missing. Install it with: brew install cloudflared")

    access_key = secrets.token_urlsafe(16)
    env = {**os.environ, "TRAY_ACCESS_KEY": access_key}
    if "--mock" in sys.argv:
        env["TRAY_MOCK"] = "1"

    app = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    tunnel = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        link = None
        for line in tunnel.stdout:
            match = LINK_PATTERN.search(line)
            if match:
                link = match.group(0)
                break
        if not link:
            sys.exit("Couldn't create the link. Check the internet connection.")

        camera_link = f"{link}/?key={access_key}"
        print("\nPHONE: scan this QR code, or open the camera link on your phone.\n")
        print_qr(camera_link)
        print(f"Camera link:  {camera_link}")
        print(f"Laptop link:  {link}/?key={access_key}&display=1")
        print("\nAnyone with these links can run scans, so don't post them.")
        print("Keep this running. Press Ctrl+C to stop.\n", flush=True)

        for _ in tunnel.stdout:  # Keeps reading so the tunnel never stalls on full output.
            pass
    except KeyboardInterrupt:
        print("\nStopped. The links no longer work.")
    finally:
        tunnel.terminate()
        app.terminate()


if __name__ == "__main__":
    main()
