from leadgen.core import db


def test_semicolon_inside_sql_comment_does_not_split_statement(pg_urls, tmp_path):
    schema = tmp_path / 'schema.sql'
    schema.write_text('-- a query log; one row per request\n'
                      'CREATE TABLE IF NOT EXISTS loader_probe (id INT);\n'
                      'DROP TABLE loader_probe;\n', encoding='utf-8')
    conn = db.connect(pg_urls['main'])
    try:
        db._apply(conn, str(schema))
    finally:
        conn.close()
