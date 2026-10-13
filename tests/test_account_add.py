from leadgen.access import account_add, accounts
from leadgen.core import settings


def test_adding_hiker_key_turns_on_hybrid_when_mode_not_chosen(stores, pg_urls, monkeypatch):
    """«В крайнем случае API»: свои аккаунты и веб-обход первыми, HikerAPI — последним."""
    monkeypatch.setenv('MAIN_DB_URL', pg_urls['main'])
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])
    account_add.main(['hiker', '--key', 'KEY'])
    assert accounts.transport_mode(stores.main) == 'hybrid'


def test_adding_hiker_key_keeps_mode_chosen_by_user(stores, pg_urls, monkeypatch):
    monkeypatch.setenv('MAIN_DB_URL', pg_urls['main'])
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])
    settings.put(stores.main, 'ig_transport', 'own')
    account_add.main(['hiker', '--key', 'KEY'])
    assert accounts.transport_mode(stores.main) == 'own'


import json


def test_session_file_roundtrip_keeps_cookies_and_user_agent(stores, pg_urls, monkeypatch, tmp_path):
    monkeypatch.setenv('MAIN_DB_URL', pg_urls['main'])
    monkeypatch.setenv('ARCHIVE_DB_URL', pg_urls['archive'])
    jar = {'sessionid': 's', 'csrftoken': 'c', 'ds_user_id': '1'}
    monkeypatch.setattr(account_add, 'cookies_from_cdp', lambda port: (jar, 'Mozilla/5.0 Test'))
    out = tmp_path / 'ig4.json'
    account_add.main(['export', '--port', '9225', '--out', str(out)])
    assert json.loads(out.read_text())['user_agent'] == 'Mozilla/5.0 Test'
    account_add.main(['own', '--label', 'ig4', '--session', str(out), '--proxy', 'http://p:1'])
    row = accounts.active_accounts(stores.archive, 'own')[0]
    assert row['cookies'] == jar and row['user_agent'] == 'Mozilla/5.0 Test' and row['proxy'] == 'http://p:1'


def test_export_tightens_permissions_of_existing_file_and_requires_user_agent(stores, monkeypatch, tmp_path):
    import os, stat
    out = tmp_path / 'ig4.json'
    out.write_text('{}')
    os.chmod(out, 0o644)
    monkeypatch.setattr(account_add, 'cookies_from_cdp', lambda port: ({'sessionid': 's', 'csrftoken': 'c', 'ds_user_id': '1'}, 'UA'))
    account_add.main(['export', '--port', '1', '--out', str(out)])
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    monkeypatch.setattr(account_add, 'cookies_from_cdp', lambda port: ({'sessionid': 's', 'csrftoken': 'c', 'ds_user_id': '1'}, ''))
    import pytest as _p
    with _p.raises(SystemExit):
        account_add.main(['export', '--port', '1', '--out', str(out)])
