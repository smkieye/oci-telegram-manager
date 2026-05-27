from app.services.account_store import AccountStore, extract_region, slugify_account_name


SAMPLE_CONFIG = """
[DEFAULT]
user=ocid1.user.oc1..example
tenancy=ocid1.tenancy.oc1..example
fingerprint=aa:bb:cc
region=ap-singapore-1
key_file=/home/me/.oci/old.pem
""".strip()

SAMPLE_KEY = b"-----BEGIN PRIVATE KEY-----\nexample\n-----END PRIVATE KEY-----\n"


def test_slugify_account_name_keeps_chinese_and_ascii_safe_chars():
    assert slugify_account_name("首尔 API 账号") == "首尔-api-账号"
    assert slugify_account_name("Singapore_01") == "singapore_01"


def test_extract_region_from_config():
    assert extract_region(SAMPLE_CONFIG) == "ap-singapore-1"


def test_account_store_creates_valid_account_and_rewrites_key_path(tmp_path):
    store = AccountStore(tmp_path / "accounts")

    account = store.create_account("Singapore", SAMPLE_CONFIG, SAMPLE_KEY)

    assert account.id == "singapore"
    assert account.name == "Singapore"
    assert account.region == "ap-singapore-1"
    assert account.config_path.exists()
    assert account.key_path.exists()
    rendered = account.config_path.read_text(encoding="utf-8")
    assert "key_file=/app/data/accounts/singapore/oci_api_key.pem" in rendered
    assert "/home/me/.oci/old.pem" not in rendered
    assert store.get_current_id() == "singapore"

    ok, message = store.validate_account("singapore")
    assert ok is True
    assert "ready" in message.lower()


def test_account_store_assigns_unique_ids_and_can_switch_delete(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    first = store.create_account("Seoul", SAMPLE_CONFIG, SAMPLE_KEY)
    second = store.create_account("Seoul", SAMPLE_CONFIG, SAMPLE_KEY)

    assert first.id == "seoul"
    assert second.id == "seoul-2"

    store.set_current(second.id)
    assert store.get_current().id == "seoul-2"

    store.delete_account(second.id)
    assert store.get_current_id() == "seoul"
    assert [account.id for account in store.list_accounts()] == ["seoul"]
