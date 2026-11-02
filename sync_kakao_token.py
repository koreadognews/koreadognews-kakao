
import json
import os
import subprocess
from pathlib import Path

TOKEN_FILE = Path("tokens.json")

def main():
    if not TOKEN_FILE.exists():
        print("Kakao refresh token unchanged; no update needed.")
        return

    token = json.loads(
        TOKEN_FILE.read_text(encoding="utf-8")
    ).get("refresh_token")

    if not token:
        raise RuntimeError("No renewed refresh token found.")

    if not os.getenv("GH_TOKEN"):
        raise RuntimeError("GH_SECRETS_TOKEN is missing.")

    subprocess.run(
        [
            "gh", "secret", "set",
            "KAKAO_REFRESH_TOKEN",
            "--repo",
            "koreadognews/koreadognews-kakao"
        ],
        input=token,
        text=True,
        capture_output=True,
        check=True,
        timeout=30
    )

    print("Kakao refresh token updated in GitHub Secrets.")

if __name__ == "__main__":
    main()
