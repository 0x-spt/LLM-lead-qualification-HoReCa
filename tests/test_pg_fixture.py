import psycopg


def test_two_separate_test_databases_are_reachable(pg_urls):
    names = set()
    for url in pg_urls.values():
        with psycopg.connect(url) as conn:
            names.add(conn.execute('SELECT current_database()').fetchone()[0])
    assert names == {'leadgen_main_test', 'leadgen_archive_test'}
