from leadgen.core import alerts, settings


def test_settings_default_when_missing(stores):
    assert settings.get(stores.main, 'ig_transport', 'own') == 'own'


def test_settings_put_then_get_overwrites(stores):
    settings.put(stores.main, 'ig_transport', 'hybrid')
    settings.put(stores.main, 'ig_transport', 'hiker')
    assert settings.get(stores.main, 'ig_transport') == 'hiker'


def test_alert_is_not_duplicated_while_unsent(stores):
    assert alerts.raise_alert(stores.archive, 'checkpoint', 'ig3: нужен вход') is True
    assert alerts.raise_alert(stores.archive, 'checkpoint', 'ig3: нужен вход') is False
    count = stores.archive.execute('SELECT COUNT(*) AS c FROM alerts').fetchone()['c']
    assert count == 1


def test_alert_is_raised_again_after_previous_was_sent(stores):
    alerts.raise_alert(stores.archive, 'checkpoint', 'ig3: нужен вход')
    stores.archive.execute('UPDATE alerts SET sent_at = now()')
    assert alerts.raise_alert(stores.archive, 'checkpoint', 'ig3: нужен вход') is True
