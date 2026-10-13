from leadgen.core import db

MAIN_TABLES = {'geo', 'venues', 'contacts', 'settings'}
ARCHIVE_TABLES = {'accounts', 'endpoint_health', 'request_log', 'work_queue',
                  'profile_cache', 'candidates', 'rejected', 'alerts'}


def _tables(conn):
    return {r['tablename'] for r in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}


def test_main_and_archive_have_their_own_tables(stores):
    assert MAIN_TABLES <= _tables(stores.main)
    assert ARCHIVE_TABLES <= _tables(stores.archive)


def test_rejected_and_secrets_never_live_in_main(stores):
    """Отклонённые, куки и ключ HikerAPI — только в архивной базе."""
    assert not ({'rejected', 'accounts', 'candidates', 'profile_cache'} & _tables(stores.main))


def test_init_is_idempotent(stores):
    db.init_main(stores.main)
    db.init_archive(stores.archive)
    assert MAIN_TABLES <= _tables(stores.main)


def test_rows_come_back_as_dicts(stores):
    row = stores.main.execute('SELECT 1 AS x').fetchone()
    assert row['x'] == 1


def test_connections_have_statement_timeout(stores):
    """Молча оборванное соединение не должно вешать воркер на четверть часа."""
    assert stores.main.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '30s'
    assert stores.archive.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '30s'
def _columns(conn, table):
    return {r['column_name'] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)).fetchall()}


def test_plan2_tables_and_columns(stores):
    assert {'role_profiles', 'verdicts'} <= _tables(stores.main)
    assert 'grader_log' in _tables(stores.archive)
    assert {'role_code', 'crm_position', 'bio', 'captions', 'grid_path',
            'needs_strong_profile'} <= _columns(stores.main, 'contacts')


def test_plan2_migration_applies_to_existing_contacts_table(stores):
    """База plan 1 уже засеяна — новые колонки обязаны доезжать ALTER-ом."""
    stores.main.execute('ALTER TABLE contacts DROP COLUMN grid_path')
    db.init_main(stores.main)
    assert 'grid_path' in _columns(stores.main, 'contacts')


def test_init_main_seeds_role_profiles(stores):
    """C1: на боевой базе таблица должностей не должна оставаться пустой."""
    from leadgen.qualify import roles
    assert roles.load_enabled(stores.main) == []  # фикстура чистит таблицы
    db.init_main(stores.main)
    assert [p['code'] for p in roles.load_enabled(stores.main)] == ['pastry_chef', 'chef', 'restorator', 'consulting', 'dealer']


def test_plan2_fix_columns(stores):
    assert {'grid_tiles', 'cap_reason'} <= _columns(stores.main, 'contacts')


def test_plan3_tables_and_columns(stores):
    assert 'exports' in _tables(stores.main)
    assert {'tg_card_at', 'tg_message_id'} <= _columns(stores.main, 'contacts')
