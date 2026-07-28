-- Run once in the Supabase SQL editor (Project -> SQL Editor -> New query).
-- Holds the latest person-detection status per device for the web dashboard.

create table if not exists device_status (
  device_id text primary key,
  person_detected boolean not null default false,
  confidence real,
  updated_at timestamptz not null default now()
);

alter table device_status enable row level security;

-- Dashboard reads with the public anon key.
create policy "device_status: public read"
  on device_status for select
  using (true);

-- No insert/update policy is defined: person_detect.py writes with the
-- Supabase service_role key, which bypasses RLS entirely.

insert into device_status (device_id, person_detected)
values ('cam1', false)
on conflict (device_id) do nothing;
