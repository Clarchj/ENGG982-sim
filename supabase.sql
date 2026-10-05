-- Haul Twin: optional shared run log. Run once in the Supabase SQL editor.
-- The web page uses only the public anon key. Row-level security lets that key
-- read and add rows, and nothing else (no update, no delete).
create table if not exists public.twin_runs (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  label         text  not null check (char_length(label) <= 120),
  scenario      text  check (char_length(scenario) <= 40),
  config        jsonb check (pg_column_size(config) <= 20000),
  summary       jsonb not null check (pg_column_size(summary) <= 20000),
  engine_version text check (char_length(engine_version) <= 20),
  seed          bigint
);

alter table public.twin_runs enable row level security;

drop policy if exists "anon can read runs"   on public.twin_runs;
drop policy if exists "anon can add runs"    on public.twin_runs;
create policy "anon can read runs" on public.twin_runs for select to anon using (true);
create policy "anon can add runs"  on public.twin_runs for insert to anon with check (true);

grant usage on schema public to anon;
grant select, insert on public.twin_runs to anon;
-- Not granted on purpose: update, delete. Anyone holding the public key can add rows,
-- so treat this table as a shared notice board, not as trusted data.
