-- Run once in the Supabase SQL editor (Project -> SQL Editor -> New query).
-- Holds the latest person-detection status per device for the web dashboard.

create table if not exists device_status (
  device_id text primary key,
  person_detected boolean not null default false,
  lying_detected boolean not null default false,
  confidence real,
  temperature real,
  humidity real,
  ai_sensitivity text not null default 'normal',
  ac_on boolean not null default false,
  ac_manual boolean not null default false,
  sleep_on boolean not null default false,
  sleep_manual boolean not null default false,
  sleep_delay_sec integer not null default 600,
  absence_enabled boolean not null default false,
  absence_delay_sec integer not null default 1800,
  sleep_auto_enabled boolean not null default false,
  updated_at timestamptz not null default now()
);

-- If the table already existed from before lying-down detection was added:
alter table device_status add column if not exists lying_detected boolean not null default false;

-- If the table already existed from before BME280 temp/humidity was added:
alter table device_status add column if not exists temperature real;
alter table device_status add column if not exists humidity real;

-- If the table already existed from before AC/sleep-mode automation was added:
-- ai_sensitivity drives the PMV comfort-band half-width (low/normal/high ->
-- 0.7/0.5/0.2 C, see server/person_detect.py). The *_manual flags mean "the
-- dashboard user has taken manual control of this output" -- once true,
-- person_detect.py stops recomputing ac_on/sleep_on automatically and just
-- drives the LEDs from whatever value the dashboard last wrote, until the
-- user flips the manual toggle again.
alter table device_status add column if not exists ai_sensitivity text not null default 'normal';
alter table device_status add column if not exists ac_on boolean not null default false;
alter table device_status add column if not exists ac_manual boolean not null default false;
alter table device_status add column if not exists sleep_on boolean not null default false;
alter table device_status add column if not exists sleep_manual boolean not null default false;
alter table device_status add column if not exists sleep_delay_sec integer not null default 600;

-- If the table already existed from before absence auto-off was added:
-- absence_enabled is the dashboard's on/off switch for the feature itself (not
-- an output state like ac_on), and absence_delay_sec is how long nobody may be
-- detected before person_detect.py forces the AC off. Note this one overrides
-- ac_manual: the point of the feature is that an empty room does not get
-- cooled, so it wins over whatever the dashboard last set by hand. It is
-- one-way -- the shutoff is written back to ac_on, so someone coming back does
-- not get the AC returned to them; the dashboard has to switch it on again.
alter table device_status add column if not exists absence_enabled boolean not null default false;
alter table device_status add column if not exists absence_delay_sec integer not null default 1800;

-- If the table already existed from before auto-sleep got its own switch:
-- sleep_auto_enabled turns on "switch sleep mode on by itself once someone has
-- been lying down for sleep_delay_sec". This used to be implicit -- it ran
-- whenever sleep_manual was false -- which meant the first dashboard toggle
-- latched sleep_manual true for good and killed the automatic trigger with no
-- way back short of editing this column by hand.
alter table device_status add column if not exists sleep_auto_enabled boolean not null default false;

-- One-time migration if the table still has the old sleep_delay_min (minutes)
-- column from an earlier version of this schema (replaced by sleep_delay_sec
-- so the dashboard can set the delay in seconds too, for quick testing):
--   alter table device_status rename column sleep_delay_min to sleep_delay_sec;
--   update device_status set sleep_delay_sec = sleep_delay_sec * 60;

alter table device_status enable row level security;

-- Dashboard reads with the public anon key.
create policy "device_status: public read"
  on device_status for select
  using (true);

-- Logged-in dashboard users can update settings (AI sensitivity, AC/sleep
-- manual toggles). person_detect.py writes everything else with the
-- Supabase service_role key, which bypasses RLS entirely.
create policy "device_status: authenticated update"
  on device_status for update
  using (auth.role() = 'authenticated')
  with check (auth.role() = 'authenticated');

insert into device_status (device_id, person_detected)
values ('cam1', false)
on conflict (device_id) do nothing;
