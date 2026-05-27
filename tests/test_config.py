import os

from app.config import Settings, parse_allowed_user_ids, mask_secret


def test_parse_allowed_user_ids_accepts_comma_and_space_separated_values():
    assert parse_allowed_user_ids("123, 456 789") == {123, 456, 789}


def test_settings_loads_required_values_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123456:telegram-token")
    monkeypatch.setenv("ALLOWED_USER_IDS", "6327047192")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    settings = Settings.from_env()

    assert settings.bot_token == "123456:telegram-token"
    assert settings.allowed_user_ids == {6327047192}
    assert settings.oci_config_path == tmp_path / "oci" / "config"
    assert settings.accounts_dir == tmp_path / "accounts"


def test_mask_secret_keeps_short_safe_hint_without_leaking_full_value():
    masked = mask_secret("1234567890:ABCDEFGHIJKLMNOP")

    assert masked.startswith("1234")
    assert masked.endswith("MNOP")
    assert "567890:ABCDEFGHIJKL" not in masked
