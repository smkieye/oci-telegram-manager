from pathlib import Path

from app.services.oci_config import build_docker_oci_config, validate_uploaded_oci_files


def test_build_docker_oci_config_rewrites_key_file_path():
    source = """
[DEFAULT]
user=ocid1.user.oc1..example
tenancy=ocid1.tenancy.oc1..example
fingerprint=aa:bb:cc
region=ap-singapore-1
key_file=/home/me/.oci/old.pem
""".strip()

    rendered = build_docker_oci_config(source, "/app/data/oci/oci_api_key.pem")

    assert "key_file=/app/data/oci/oci_api_key.pem" in rendered
    assert "/home/me/.oci/old.pem" not in rendered
    assert "region=ap-singapore-1" in rendered


def test_validate_uploaded_oci_files_requires_config_and_key(tmp_path):
    oci_dir = tmp_path / "oci"
    oci_dir.mkdir()
    (oci_dir / "config").write_text("[DEFAULT]\nkey_file=/app/data/oci/oci_api_key.pem\n", encoding="utf-8")

    ok, message = validate_uploaded_oci_files(oci_dir)
    assert ok is False
    assert "oci_api_key.pem" in message

    (oci_dir / "oci_api_key.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    ok, message = validate_uploaded_oci_files(oci_dir)
    assert ok is True
    assert "ready" in message.lower()
