-- Archive database (local Postgres): everything that didn't pass, plus internal bookkeeping

CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'own' CHECK (kind IN ('own', 'hiker')),
    cookies JSONB,
    user_agent TEXT,
    proxy TEXT,
    api_key TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS endpoint_health (
    account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    endpoint_class TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    requests_count INTEGER NOT NULL DEFAULT 0,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    PRIMARY KEY (account_id, endpoint_class)
);

CREATE TABLE IF NOT EXISTS request_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    account_id INTEGER,
    endpoint_class TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS request_log_at ON request_log (at);

CREATE TABLE IF NOT EXISTS work_queue (
    id BIGSERIAL PRIMARY KEY,
    task_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_agent TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS work_queue_pick ON work_queue (status, priority, id);

CREATE TABLE IF NOT EXISTS profile_cache (
    pk TEXT PRIMARY KEY,
    nick TEXT,
    full_name TEXT,
    city TEXT,
    bio TEXT,
    category TEXT,
    followers INTEGER,
    posts INTEGER,
    is_private BOOLEAN,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS profile_cache_nick ON profile_cache (nick);

CREATE TABLE IF NOT EXISTS candidates (
    nick TEXT PRIMARY KEY,
    pk TEXT,
    full_name TEXT,
    bio TEXT,
    category TEXT,
    city_name TEXT,
    role_hint TEXT,
    venue TEXT,
    city TEXT,
    source TEXT,
    agent TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS candidates_pk ON candidates (pk);

CREATE TABLE IF NOT EXISTS rejected (
    nick TEXT PRIMARY KEY,
    pk TEXT,
    full_name TEXT,
    venue TEXT,
    city TEXT,
    reason TEXT NOT NULL,
    stage TEXT NOT NULL,
    source TEXT,
    legacy JSONB,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rejected_pk ON rejected (pk);
-- Human review of rejects (criteria calibration): bio and grid for the card,
-- when the card was sent to the bot, and what the client decided ("верно" / "берём")
ALTER TABLE rejected ADD COLUMN IF NOT EXISTS bio TEXT;
ALTER TABLE rejected ADD COLUMN IF NOT EXISTS grid_path TEXT;
ALTER TABLE rejected ADD COLUMN IF NOT EXISTS review TEXT;
ALTER TABLE rejected ADD COLUMN IF NOT EXISTS review_sent_at TIMESTAMPTZ;
ALTER TABLE rejected ADD COLUMN IF NOT EXISTS review_message_id BIGINT;

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ
);

-- Plan 2: grader call log (the daily cap is computed from it)
CREATE TABLE IF NOT EXISTS grader_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stage TEXT NOT NULL,
    model TEXT NOT NULL,
    profiles INTEGER NOT NULL,
    ok BOOLEAN NOT NULL,
    cost_usd REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS grader_log_at ON grader_log (at);

-- Plan 4: 2GIS crawl schedule (a "city × category" pair — once every 30 days)
CREATE TABLE IF NOT EXISTS registry_runs (
    slug TEXT NOT NULL,
    query TEXT NOT NULL,
    harvested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL,
    found INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (slug, query)
);

-- People search via a search engine (Brave): a query log — a 429 on an exit node cools it down
CREATE TABLE IF NOT EXISTS websearch_runs (
    id BIGSERIAL PRIMARY KEY,
    query TEXT NOT NULL,
    page INTEGER NOT NULL,
    port INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    found INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS websearch_runs_query ON websearch_runs (query, page, status);

-- Self-recovery for accounts whose VPN exit died (2026-09-30): since when it's been down,
-- and whether the "down for over 15 minutes" alert already fired
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS down_at TIMESTAMPTZ;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS down_alerted BOOLEAN NOT NULL DEFAULT false;

-- Anonymous readers (2026-10-01): no cookies, only pages viewable while logged out
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_kind_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_kind_check CHECK (kind IN ('own', 'hiker', 'anon'));

-- Warmup for new accounts (2026-10-05): until this time, the account only reads profiles.
-- Set when a new account is added — a repeat login doesn't touch it.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS warmup_until TIMESTAMPTZ;

-- Second search engine (2026-10-05): DuckDuckGo alongside Brave, coverage and cooldown tracked per engine
ALTER TABLE websearch_runs ADD COLUMN IF NOT EXISTS engine TEXT NOT NULL DEFAULT 'brave';

-- Hourly request cap per own account (2026-10-08): computed from the log — needs an index
CREATE INDEX IF NOT EXISTS request_log_account_at ON request_log (account_id, at);
