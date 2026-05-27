from __future__ import annotations

import configparser
from pathlib import Path


def build_docker_oci_config(raw_config: str, container_key_path: str) -> str:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(raw_config)
    if "DEFAULT" not in parser:
        raise ValueError("OCI config must contain [DEFAULT]")
    parser["DEFAULT"]["key_file"] = container_key_path

    # configparser does not render DEFAULT as a normal section unless another
    # section exists, so render explicitly to preserve OCI CLI format.
    lines = ["[DEFAULT]"]
    for key, value in parser.defaults().items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def validate_uploaded_oci_files(oci_dir: Path) -> tuple[bool, str]:
    config_path = oci_dir / "config"
    key_path = oci_dir / "oci_api_key.pem"
    if not config_path.exists():
        return False, "Missing OCI config file: config"
    if not key_path.exists():
        return False, "Missing OCI private key file: oci_api_key.pem"
    if "BEGIN" not in key_path.read_text(encoding="utf-8", errors="ignore"):
        return False, "oci_api_key.pem does not look like a private key"
    return True, "OCI files ready"


def save_uploaded_oci_config(
    raw_config: str,
    target_dir: Path,
    container_key_path: str = "/app/data/oci/oci_api_key.pem",
) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    rendered = build_docker_oci_config(raw_config, container_key_path)
    config_path = target_dir / "config"
    config_path.write_text(rendered, encoding="utf-8")
    config_path.chmod(0o600)
    return config_path


def save_uploaded_oci_key(raw_key: bytes, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    key_path = target_dir / "oci_api_key.pem"
    key_path.write_bytes(raw_key)
    key_path.chmod(0o600)
    return key_path
