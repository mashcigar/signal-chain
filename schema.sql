-- signal-chain spine
--
-- Run this once in the SQL editor of a Supabase project you own. Then set SUPABASE_URL and
-- SUPABASE_SERVICE_ROLE_KEY and the chain writes here instead of to a local file. Nothing else
-- changes, which is the point: the storage layer moved, the agents did not.
--
-- Four decisions are worth reading rather than skimming, because they are the difference between
-- a demo database and one that could survive a security review.

-- ─────────────────────────────────────────── 1. the tenant boundary
-- Every row in every table below carries workspace_id. Not because this build has two tenants,
-- but because retrofitting a tenant boundary onto a live table is a migration nobody enjoys, and
-- adding the column on day one costs nothing.

create table if not exists workspaces (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

-- ─────────────────────────────────────────── 2. provenance as columns, not convention
-- source_url, retrieved_at and evidence are not metadata bolted on the side. They are what makes
-- a row a claim rather than an opinion. A row that cannot say where it came from is exactly the
-- row the verification stage exists to delete.

create table if not exists claims (
  id            text primary key,          -- sha256 derived, stable across runs
  workspace_id  uuid not null references workspaces(id) on delete cascade,

  target        text not null,
  dimension     text not null,
  claim_text    text not null,
  method        text not null,             -- 'fetch+match' | 'hand-entered'

  source_url    text,                      -- provenance: where
  retrieved_at  timestamptz,               -- provenance: when
  evidence      text,                      -- provenance: the exact characters matched

  first_seen    timestamptz not null,
  last_seen     timestamptz not null
);

create index if not exists idx_claims_workspace on claims(workspace_id);
create index if not exists idx_claims_target on claims(workspace_id, target);

-- ─────────────────────────────────────────── 3. append only, enforced by the grant
-- The audit trail is not append only because the application is well behaved. It is append only
-- because UPDATE and DELETE are revoked below, including from the service role. Code that tries
-- to rewrite history gets an error from the database rather than a code review comment.

create table if not exists claim_events (
  id            bigint generated always as identity primary key,
  workspace_id  uuid not null references workspaces(id) on delete cascade,
  claim_id      text not null,
  run_id        text not null,
  at            timestamptz not null default now(),
  action        text not null,             -- 'added' | 'refreshed' | 'kept' | 'dropped'
  reason        text                       -- required in practice for 'dropped'
);

create index if not exists idx_events_claim on claim_events(claim_id);
create index if not exists idx_events_run on claim_events(run_id);

-- ─────────────────────────────────────────── 4. default deny
-- RLS on, and deliberately no policies for anon or authenticated. That is not an oversight, it is
-- the posture: nothing is readable from a browser. The chain reaches this database with the
-- service role key, which lives in the server environment and never in a client bundle.
--
-- When a real front end needs read access, it gets a policy scoped to its workspace, written on
-- purpose. Until then the answer to "who can read this" is nobody.

alter table workspaces   enable row level security;
alter table claims       enable row level security;
alter table claim_events enable row level security;

revoke update, delete on claim_events from anon, authenticated, service_role;

-- ─────────────────────────────────────────── seed
insert into workspaces (id, name)
values ('00000000-0000-0000-0000-000000000001', 'signal-chain demo')
on conflict (id) do nothing;
