import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compose():
    with open(os.path.join(ROOT, 'docker-compose.yml'), encoding='utf-8') as f:
        return yaml.safe_load(f)


def test_services():
    s = compose()['services']
    assert set(s) == {'archive-db', 'workers', 'registry', 'grader', 'bot', 'backup'}
    assert s['workers']['command'] == ['python', '-m', 'leadgen.ops.workers']
    assert s['registry']['command'] == ['python', '-m', 'leadgen.sources.registry']
    assert s['grader']['command'] == ['python', '-m', 'leadgen.llm.grader', 'loop']
    assert s['bot']['command'] == ['python', '-m', 'leadgen.ops.bot']
    for name in ('workers', 'registry', 'grader', 'bot', 'backup'):
        assert s[name]['restart'] == 'unless-stopped'
        assert s[name]['env_file'] == ['.env']
        assert 'archive-db' in s[name]['depends_on']


def test_grids_shared_between_workers_and_grader():
    s = compose()['services']
    assert 'grids:/data/grids' in s['workers']['volumes'] and 'grids:/data/grids' in s['grader']['volumes']
    assert s['workers']['environment']['LEADGEN_GRIDS_DIR'] == '/data/grids'


def test_archive_db_not_exposed_and_persistent():
    db = compose()['services']['archive-db']
    assert 'ports' not in db
    assert 'archive:/var/lib/postgresql/data' in db['volumes']


def test_env_example_lists_every_secret_and_no_values():
    text = open(os.path.join(ROOT, '.env.example'), encoding='utf-8').read()
    for key in ('MAIN_DB_URL', 'ARCHIVE_DB_URL', 'POSTGRES_PASSWORD', 'TELEGRAM_TOKEN',
                'TELEGRAM_OWNER_ID', 'GOOGLE_SA_JSON'):
        assert key + '=' in text
    assert 'sessionid' not in text


def test_env_is_ignored_by_git_and_docker():
    assert '.env' in open(os.path.join(ROOT, '.gitignore'), encoding='utf-8').read().split()
    assert '.env' in open(os.path.join(ROOT, '.dockerignore'), encoding='utf-8').read().split()


def test_seed_inputs_reachable_from_workers():
    """docs/deploy.md засевает базы через контейнер workers из ./export."""
    assert './export:/data/export' in compose()['services']['workers']['volumes']


def test_image_pins_debian_matching_pgdg_and_bakes_no_lead_data():
    """№8: база образа и репозиторий PostgreSQL — один дистрибутив; данные лидов не в образе."""
    df = open(os.path.join(ROOT, 'Dockerfile'), encoding='utf-8').read()
    assert 'python:3.12-slim-bookworm' in df and 'bookworm-pgdg' in df and 'setup_22.x' in df
    ignore = open(os.path.join(ROOT, '.dockerignore'), encoding='utf-8').read().split()
    assert 'db/*.csv' in ignore and 'db/backups' in ignore


def test_claude_token_documented():
    """№7: вход оценщика — токен в .env, а не файл в контейнере."""
    assert 'CLAUDE_CODE_OAUTH_TOKEN=' in open(os.path.join(ROOT, '.env.example'), encoding='utf-8').read()
