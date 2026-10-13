-- Main database (Supabase): accepted contacts, the venue registry, geography, settings

CREATE TABLE IF NOT EXISTS geo (
    city TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    department TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS venues (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT,
    level TEXT,
    avg_check INTEGER,
    rating TEXT,
    ig_account TEXT,
    pk TEXT,
    chef_chefru TEXT,
    status TEXT,
    source TEXT,
    UNIQUE (name, city)
);
CREATE INDEX IF NOT EXISTS venues_ig ON venues (lower(ig_account));

CREATE TABLE IF NOT EXISTS contacts (
    nick TEXT PRIMARY KEY,
    pk TEXT,
    type TEXT,
    name TEXT,
    role TEXT,
    venue TEXT,
    city TEXT,
    department TEXT,
    level TEXT,
    status TEXT NOT NULL,
    legacy_status TEXT,
    reason TEXT,
    last_activity TEXT,
    checked_date TEXT,
    found_via TEXT,
    agent TEXT,
    delivered_to TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contacts_pk ON contacts (pk);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Plan 2: contact fields for the grader (ALTER — the database is already seeded)
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS role_code TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS crm_position TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS bio TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS captions JSONB;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS grid_path TEXT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS needs_strong_profile BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS contacts_status ON contacts (status);

CREATE TABLE IF NOT EXISTS role_profiles (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    priority INTEGER NOT NULL DEFAULT 100,
    role_markers TEXT NOT NULL,
    exclude_markers TEXT NOT NULL DEFAULT '',
    stop_markers TEXT NOT NULL DEFAULT '',
    venue_levels TEXT[] NOT NULL DEFAULT ARRAY['PREMIUM', 'UPPER-MIDDLE'],
    needs_feed BOOLEAN NOT NULL DEFAULT true,
    needs_external_proof BOOLEAN NOT NULL DEFAULT false,
    grader_prompt TEXT NOT NULL DEFAULT '',
    reference_top TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    reference_bottom TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    crm_role TEXT NOT NULL,
    crm_position TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS verdicts (
    id BIGSERIAL PRIMARY KEY,
    nick TEXT NOT NULL,
    stage TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL,
    strong_profile BOOLEAN,
    reason TEXT,
    model TEXT,
    by_human BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS verdicts_nick ON verdicts (nick);

-- Plan 2 review fixes: how many grid tiles are actually filled, and why a
-- contact must go through human review (city undetermined, role known only from the venue's word)
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS grid_tiles INTEGER;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS cap_reason TEXT;

-- Plan 3: Telegram card for borderline cases, and the export log
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_card_at TIMESTAMPTZ;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_message_id BIGINT;

CREATE TABLE IF NOT EXISTS exports (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger TEXT NOT NULL,
    counts JSONB NOT NULL,
    nicks TEXT[] NOT NULL
);

-- Address from 2GIS — a second source for confirming the city for the website bridge
ALTER TABLE venues ADD COLUMN IF NOT EXISTS address TEXT;

-- Website bridge: the 2GIS card id, and a flag that the website was already checked
ALTER TABLE venues ADD COLUMN IF NOT EXISTS gis_id TEXT;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS site_checked_at TIMESTAMPTZ;

-- The role is only searched for in these departments (empty — everywhere). Consulting — East (НСК) only, 2026-09-30
ALTER TABLE role_profiles ADD COLUMN IF NOT EXISTS departments TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
