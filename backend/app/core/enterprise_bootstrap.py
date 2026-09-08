"""Prepare company app configuration without printing application secrets."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import re
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.storage import DATA_DIR
from app.skills.lark_cli.profiles import isolated_lark_cli_config_path, profile_for_user, cli_home_for_profile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-account", required=True)
    parser.add_argument("--tenant-key", required=True)
    parser.add_argument("--redirect-uri", required=True)
    parser.add_argument("--repair-incomplete", action="store_true")
    args = parser.parse_args()
    config = json.loads(isolated_lark_cli_config_path(profile_for_user(args.from_account)).read_text())
    apps = config.get("apps", [])
    if len(apps) != 1 or not apps[0].get("appId") or not apps[0].get("appSecret"):
        raise SystemExit("Source profile must have exactly one configured application")
    destination = DATA_DIR / "enterprise.env"
    if destination.exists():
        existing = dotenv_values(destination)
        if not args.repair_incomplete or set(existing) - {"FEISHU_APP_ID"}:
            raise SystemExit("Enterprise configuration already exists; preserve its encryption key")
    secret = apps[0]["appSecret"]
    if isinstance(secret, dict) and secret.get("source") == "keychain":
        if secret.get("id") != "appsecret:" + apps[0]["appId"]:
            raise SystemExit("Application secret reference does not match app ID")
        # CLI 1.0.93 macOS stores encrypted entries under its isolated HOME.
        directory = cli_home_for_profile(profile_for_user(args.from_account)) / "Library/Application Support/lark-cli"
        master_path = directory / "master.key.file"
        if master_path.exists():
            master = master_path.read_bytes()
        else:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "lark-cli", "-a", "master.key", "-w"],
                capture_output=True, text=True, timeout=20,
            )
            if result.returncode:
                raise SystemExit("Could not read application secret encryption key")
            master = base64.b64decode(result.stdout.strip(), validate=True)
        encrypted = (directory / (re.sub(r"[^a-zA-Z0-9._-]", "_", secret["id"]) + ".enc")).read_bytes()
        secret = AESGCM(master).decrypt(encrypted[:12], encrypted[12:], None).decode()
    if not isinstance(secret, str) or not secret:
        raise SystemExit("Unsupported or empty application secret")
    values = {
        "FEISHU_APP_ID": apps[0]["appId"],
        "FEISHU_APP_SECRET": secret,
        "FEISHU_TENANT_KEY": args.tenant_key,
        "FEISHU_REDIRECT_URI": args.redirect_uri,
        "FEISHU_TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    fd, name = tempfile.mkstemp(prefix="enterprise-", dir=DATA_DIR)
    os.close(fd)
    temporary = Path(name)
    try:
        for key, value in values.items():
            set_key(temporary, key, value)
        os.chmod(temporary, 0o600)
        saved = dotenv_values(temporary)
        if not all(saved.get(key) == value for key, value in values.items()):
            raise SystemExit("Configuration verification failed")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"prepared": True, "app_id": values["FEISHU_APP_ID"],
                      "path": str(destination), "personal_tokens_copied": False}))


if __name__ == "__main__":
    main()
