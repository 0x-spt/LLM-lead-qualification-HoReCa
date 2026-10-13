"""Временный Postgres для тестов.

Боевые базы тесты не трогают никогда: сервер поднимается во временной папке
(pgserver) или берётся из LEADGEN_TEST_PG, где создаются две отдельные
тестовые базы."""
import os

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

TEST_DBS = {'main': 'leadgen_main_test', 'archive': 'leadgen_archive_test'}


def _server_url(tmp_path_factory):
    url = os.environ.get('LEADGEN_TEST_PG')
    if url:
        return url, None
    import pgserver
    server = pgserver.get_server(str(tmp_path_factory.mktemp('pg')), cleanup_mode='stop')
    return server.get_uri(), server


@pytest.fixture(scope='session')
def pg_urls(tmp_path_factory):
    base, server = _server_url(tmp_path_factory)
    with psycopg.connect(base, autocommit=True) as admin:
        for name in TEST_DBS.values():
            admin.execute('DROP DATABASE IF EXISTS %s' % name)
            admin.execute('CREATE DATABASE %s' % name)
    yield {role: make_conninfo(base, dbname=name) for role, name in TEST_DBS.items()}
    if server is not None:
        server.cleanup()


def _truncate_all(conn):
    tables = [r['tablename'] for r in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
    if tables:
        conn.execute('TRUNCATE %s RESTART IDENTITY CASCADE' % ', '.join(tables))


@pytest.fixture
def stores(pg_urls):
    from leadgen.core import db
    s = db.open_stores(pg_urls['main'], pg_urls['archive'])
    db.init_main(s.main)
    db.init_archive(s.archive)
    _truncate_all(s.main)
    _truncate_all(s.archive)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _no_real_contacts_csv(tmp_path, monkeypatch):
    monkeypatch.setenv('LEADGEN_CONTACTS_CSV', str(tmp_path / 'no-such-contacts.csv'))
    monkeypatch.setenv('LEADGEN_WALKED_LEDGER', str(tmp_path / 'no-such-walked.txt'))
