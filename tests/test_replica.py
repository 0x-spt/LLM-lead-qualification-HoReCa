"""Реплика основной базы (30.09.2026): рабочая основная база — на Mac, Supabase — копия.
Воркеры простаивали ~80% времени из-за обрывов пулера Supabase через VPN."""
from leadgen.core import db, replica


def seed(main):
    main.execute("INSERT INTO geo (city, region, department) VALUES ('Новосибирск', 'Восток', 'НСК')")
    main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('A', 'Новосибирск', 'PREMIUM', 'a_rest')")
    main.execute("INSERT INTO contacts (nick, status, venue, captions) VALUES ('ivan', 'к_выгрузке', 'A', '[\"x\"]')")
    main.execute("INSERT INTO verdicts (nick, stage, verdict, reason, by_human) VALUES ('ivan', 'человек', 'accept', 'берём', true)")


def replica_db(stores):
    # вторая «основная база» для теста — схема main в базе архива (имена таблиц не пересекаются)
    db.init_main(stores.archive)
    return stores.archive


def test_copy_moves_every_table_and_row(stores):
    seed(stores.main)
    dst = replica_db(stores)
    counts = replica.copy_all(stores.main, dst)
    assert counts['contacts'] == (1, 1) and counts['venues'] == (1, 1) and counts['verdicts'] == (1, 1)
    row = dst.execute("SELECT status, captions FROM contacts WHERE nick = 'ivan'").fetchone()
    assert row['status'] == 'к_выгрузке' and row['captions'] == ['x']


def test_copy_replaces_rows_removed_at_source(stores):
    seed(stores.main)
    dst = replica_db(stores)
    replica.copy_all(stores.main, dst)
    stores.main.execute("DELETE FROM contacts WHERE nick = 'ivan'")
    replica.copy_all(stores.main, dst)
    assert dst.execute('SELECT count(*) AS c FROM contacts').fetchone()['c'] == 0


def test_sequences_continue_after_copy(stores):
    seed(stores.main)
    dst = replica_db(stores)
    replica.copy_all(stores.main, dst)
    new_id = dst.execute("INSERT INTO verdicts (nick, stage, verdict, reason, by_human) "
                         "VALUES ('p', 'человек', 'accept', 'x', true) RETURNING id").fetchone()['id']
    top = stores.main.execute('SELECT max(id) AS m FROM verdicts').fetchone()['m']
    assert new_id > top


def test_failed_copy_leaves_destination_untouched(stores, monkeypatch):
    seed(stores.main)
    dst = replica_db(stores)
    replica.copy_all(stores.main, dst)
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('petr', 'спорный')")
    monkeypatch.setattr(replica, 'TABLES', replica.TABLES + ('нет_такой_таблицы',))
    import pytest
    with pytest.raises(Exception):
        replica.copy_all(stores.main, dst)
    assert dst.execute('SELECT count(*) AS c FROM contacts').fetchone()['c'] == 1
