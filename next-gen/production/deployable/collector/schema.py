"""Postgres schema v3 for immutable collector facts."""

from __future__ import annotations

FACT_TABLES = (
    "collector_runs",
    "raw_payloads",
    "events",
    "market_snapshots",
    "weather_snapshots",
    "settlements",
    "final_temperature_labels",
    "provider_errors",
)

INIT_SQL = r"""
create table if not exists collector_runs (
  collector_run_id text primary key,
  schema_version integer not null,
  started_at_utc timestamptz not null,
  completed_at_utc timestamptz,
  snapshot_time_utc timestamptz not null,
  server_hostname text,
  collector_version text not null,
  collector_source_hash text,
  config_hash text,
  city_count_attempted integer,
  city_count_completed integer,
  provider_error_count integer,
  raw_payload_count integer,
  normalized_row_count integer,
  spool_status text not null,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists raw_payloads (
  raw_payload_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text,
  event_ticker text,
  target_date date,
  snapshot_time_utc timestamptz not null,
  snapshot_time_local timestamptz,
  provider text not null,
  endpoint_name text not null,
  method text not null default 'GET',
  url text not null,
  params jsonb not null default '{}'::jsonb,
  requested_at_utc timestamptz not null,
  received_at_utc timestamptz not null,
  latency_ms integer,
  status_code integer,
  success boolean not null,
  error_type text,
  error_message text,
  content_sha256 text not null,
  compressed_size_bytes integer not null,
  storage_bucket text not null,
  storage_path text not null,
  schema_version integer not null
);

create table if not exists events (
  event_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  city_name text not null,
  station_id text not null,
  latitude double precision not null,
  longitude double precision not null,
  city_timezone text not null,
  series_ticker text not null,
  event_ticker text not null,
  target_date date not null,
  target_date_local date not null,
  snapshot_time_utc timestamptz not null,
  snapshot_time_local timestamptz not null,
  snapshot_local_date date not null,
  snapshot_local_hour integer not null,
  climate_day_start_utc timestamptz not null,
  climate_day_end_utc timestamptz not null,
  climate_day_start_local timestamptz not null,
  climate_day_end_local timestamptz not null,
  market_close_time_utc timestamptz,
  is_active_climate_window boolean not null,
  hours_since_climate_start double precision,
  hours_until_climate_end double precision,
  checkpoint_label text not null,
  settlement_sources jsonb not null default '{}'::jsonb,
  rules_primary text,
  rules_secondary text,
  settlement_source_provider text,
  settlement_station_id text,
  raw_payload_id text references raw_payloads(raw_payload_id),
  metadata jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_time_utc)
);

create table if not exists market_snapshots (
  market_snapshot_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  target_date date not null,
  target_date_local date not null,
  event_ticker text not null,
  market_ticker text not null,
  snapshot_time_utc timestamptz not null,
  snapshot_time_local timestamptz not null,
  snapshot_local_date date not null,
  snapshot_local_hour integer not null,
  city_timezone text not null,
  climate_day_start_utc timestamptz not null,
  climate_day_end_utc timestamptz not null,
  climate_day_start_local timestamptz not null,
  climate_day_end_local timestamptz not null,
  hours_since_climate_start double precision,
  hours_until_climate_end double precision,
  checkpoint_label text not null,
  bracket_index integer not null,
  bracket_label text,
  bracket_lower_f integer,
  bracket_upper_f integer,
  is_lower_tail boolean not null,
  is_upper_tail boolean not null,
  yes_bid_dollars double precision,
  yes_ask_dollars double precision,
  no_bid_dollars double precision,
  no_ask_dollars double precision,
  last_price_dollars double precision,
  previous_yes_bid_dollars double precision,
  previous_yes_ask_dollars double precision,
  previous_price_dollars double precision,
  volume double precision,
  volume_24h double precision,
  liquidity_dollars double precision,
  open_interest double precision,
  yes_bid_size double precision,
  yes_ask_size double precision,
  no_bid_size double precision,
  no_ask_size double precision,
  yes_midpoint double precision,
  yes_spread double precision,
  normalized_market_midpoint_probability double precision,
  market_top_ticker text,
  market_top_probability double precision,
  market_top_two_gap double precision,
  sum_yes_asks double precision,
  market_overround_ask double precision,
  settlement_sources jsonb not null default '{}'::jsonb,
  rules_primary text,
  rules_secondary text,
  raw_payload_id text references raw_payloads(raw_payload_id),
  metadata jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_time_utc, market_ticker)
);

create table if not exists weather_snapshots (
  weather_snapshot_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  target_date date not null,
  target_date_local date not null,
  event_ticker text not null,
  snapshot_time_utc timestamptz not null,
  snapshot_time_local timestamptz not null,
  snapshot_local_date date not null,
  snapshot_local_hour integer not null,
  city_timezone text not null,
  climate_day_start_utc timestamptz not null,
  climate_day_end_utc timestamptz not null,
  climate_day_start_local timestamptz not null,
  climate_day_end_local timestamptz not null,
  hours_since_climate_start double precision,
  hours_until_climate_end double precision,
  checkpoint_label text not null,
  nws_anchor_high_f double precision,
  nws_daily_daytime_high_f double precision,
  nws_hourly_window_max_f double precision,
  nws_next_3h_max_f double precision,
  nws_next_6h_max_f double precision,
  nws_next_8h_max_f double precision,
  nws_remaining_day_max_f double precision,
  observed_high_so_far_f double precision,
  settlement_observed_high_so_far_f double precision,
  settlement_observed_latest_time_utc timestamptz,
  settlement_observed_age_seconds double precision,
  settlement_observed_age_hours double precision,
  settlement_observed_source_count integer,
  settlement_observed_source_range_f double precision,
  settlement_observed_source_stddev_f double precision,
  settlement_observed_nws_delta_f double precision,
  settlement_observed_sources jsonb not null default '{}'::jsonb,
  latest_observation_time_utc timestamptz,
  latest_observation_temp_f double precision,
  observation_age_seconds double precision,
  warming_rate_last_1h_f_per_hour double precision,
  warming_rate_last_3h_f_per_hour double precision,
  ensemble_raw_median_high_f double precision,
  ensemble_raw_mean_high_f double precision,
  ensemble_member_stddev_f double precision,
  ensemble_family_count integer,
  ensemble_member_count integer,
  hrrr_projected_high_f double precision,
  hrrr_next_3h_max_f double precision,
  hrrr_next_6h_max_f double precision,
  hrrr_next_8h_max_f double precision,
  hrrr_next_3h_slope_f_per_hour double precision,
  hrrr_next_6h_slope_f_per_hour double precision,
  hrrr_next_8h_slope_f_per_hour double precision,
  nbm_projected_high_f double precision,
  nbm_next_3h_max_f double precision,
  nbm_next_6h_max_f double precision,
  nbm_next_8h_max_f double precision,
  nbm_next_3h_slope_f_per_hour double precision,
  nbm_next_6h_slope_f_per_hour double precision,
  nbm_next_8h_slope_f_per_hour double precision,
  all_weather_sources_range_f double precision,
  weather_source_stddev_f double precision,
  nws_hrrr_disagreement_f double precision,
  nws_nbm_disagreement_f double precision,
  hrrr_nbm_disagreement_f double precision,
  family_baseline_high_f double precision,
  family_numerical_anchor_high_f double precision,
  family_nws_minus_nbm_f double precision,
  family_hrrr_minus_nbm_f double precision,
  family_ensemble_minus_nbm_f double precision,
  family_numerical_disagreement_f double precision,
  family_forecast_count integer,
  family_independent_ensemble integer,
  family_disagreement_range_f double precision,
  family_disagreement_std_f double precision,
  source_payload_ids jsonb not null default '{}'::jsonb,
  features jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_time_utc)
);

alter table if exists weather_snapshots
  add column if not exists family_baseline_high_f double precision,
  add column if not exists family_numerical_anchor_high_f double precision,
  add column if not exists family_nws_minus_nbm_f double precision,
  add column if not exists family_hrrr_minus_nbm_f double precision,
  add column if not exists family_ensemble_minus_nbm_f double precision,
  add column if not exists family_numerical_disagreement_f double precision,
  add column if not exists family_forecast_count integer,
  add column if not exists family_independent_ensemble integer,
  add column if not exists family_disagreement_range_f double precision,
  add column if not exists family_disagreement_std_f double precision,
  add column if not exists settlement_observed_high_so_far_f double precision,
  add column if not exists settlement_observed_latest_time_utc timestamptz,
  add column if not exists settlement_observed_age_seconds double precision,
  add column if not exists settlement_observed_age_hours double precision,
  add column if not exists settlement_observed_source_count integer,
  add column if not exists settlement_observed_source_range_f double precision,
  add column if not exists settlement_observed_source_stddev_f double precision,
  add column if not exists settlement_observed_nws_delta_f double precision,
  add column if not exists settlement_observed_sources jsonb not null default '{}'::jsonb;

alter table if exists events
  add column if not exists settlement_sources jsonb not null default '{}'::jsonb,
  add column if not exists rules_primary text,
  add column if not exists rules_secondary text,
  add column if not exists settlement_source_provider text,
  add column if not exists settlement_station_id text;

alter table if exists market_snapshots
  add column if not exists settlement_sources jsonb not null default '{}'::jsonb,
  add column if not exists rules_primary text,
  add column if not exists rules_secondary text;

alter table if exists settlements
  add column if not exists market_settlement_source text,
  add column if not exists rules_primary text,
  add column if not exists rules_secondary text;

create table if not exists settlements (
  settlement_id text primary key,
  city text not null,
  target_date date not null,
  event_ticker text not null,
  settled_at_utc timestamptz not null,
  winner_ticker text not null,
  winner_label text,
  settlement_temperature_f double precision,
  settlement_bracket_index integer,
  market_settlement_source text,
  rules_primary text,
  rules_secondary text,
  source_provider text not null,
  raw_payload_id text references raw_payloads(raw_payload_id),
  validation_status text not null,
  warnings jsonb not null default '[]'::jsonb,
  unique(city, event_ticker)
);

create table if not exists final_temperature_labels (
  final_temperature_label_id text primary key,
  city text not null,
  target_date date not null,
  event_ticker text not null,
  station_id text not null,
  final_high_f double precision not null,
  source_provider text not null,
  product_id text,
  issued_at_utc timestamptz,
  raw_payload_id text references raw_payloads(raw_payload_id),
  validation_status text not null,
  warnings jsonb not null default '[]'::jsonb,
  created_at_utc timestamptz not null,
  unique(city, event_ticker, source_provider)
);

create table if not exists provider_errors (
  provider_error_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text,
  event_ticker text,
  target_date date,
  snapshot_time_utc timestamptz not null,
  provider text not null,
  endpoint_name text not null,
  error_type text not null,
  error_message text not null,
  requested_at_utc timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_events_unsettled on events (target_date, city, event_ticker);
create index if not exists idx_weather_snapshot_time on weather_snapshots (snapshot_time_utc);
create index if not exists idx_market_snapshot_time on market_snapshots (snapshot_time_utc);
create index if not exists idx_settlements_city_event on settlements (city, event_ticker);
create index if not exists idx_final_temperature_labels_city_event
  on final_temperature_labels (city, event_ticker);

drop view if exists v_backtest_export;
drop view if exists v_collector_health;
drop view if exists v_latest_city_snapshots;
drop view if exists v_snapshots_with_settlements;
drop view if exists v_events_missing_weather_company_final_high;
drop view if exists v_events_missing_final_high;
drop view if exists v_unsettled_events;

create or replace view v_unsettled_events as
select distinct on (e.city, e.event_ticker)
  e.city,
  e.event_ticker,
  e.target_date,
  e.city_timezone,
  e.climate_day_end_utc,
  e.market_close_time_utc,
  e.settlement_source_provider,
  e.settlement_station_id
from events e
left join settlements s on s.city = e.city and s.event_ticker = e.event_ticker
where s.settlement_id is null
  and coalesce(e.market_close_time_utc, e.climate_day_end_utc) <= now()
order by e.city, e.event_ticker, e.snapshot_time_utc desc;

create or replace view v_events_missing_final_high as
select distinct on (e.city, e.event_ticker)
  e.city,
  e.event_ticker,
  e.target_date,
  e.station_id,
  e.city_timezone,
  e.climate_day_end_utc,
  e.market_close_time_utc,
  e.metadata as event_metadata,
  s.winner_ticker,
  s.settlement_temperature_f as kalshi_settlement_temperature_f,
  s.settlement_bracket_index
from events e
left join final_temperature_labels f
  on f.city = e.city
  and f.event_ticker = e.event_ticker
  and f.source_provider = 'nws_cli'
left join settlements s on s.city = e.city and s.event_ticker = e.event_ticker
where f.final_temperature_label_id is null
  and e.climate_day_end_utc <= now()
order by e.city, e.event_ticker, e.snapshot_time_utc desc;

create or replace view v_events_missing_weather_company_final_high as
select distinct on (e.city, e.event_ticker)
  e.city,
  e.event_ticker,
  e.target_date,
  e.station_id,
  e.city_timezone,
  e.climate_day_end_utc,
  e.market_close_time_utc,
  e.metadata as event_metadata,
  s.winner_ticker,
  s.settlement_temperature_f as kalshi_settlement_temperature_f,
  s.settlement_bracket_index
from events e
left join final_temperature_labels f
  on f.city = e.city
  and f.event_ticker = e.event_ticker
  and f.source_provider = 'weather_company_daily'
left join settlements s on s.city = e.city and s.event_ticker = e.event_ticker
where f.final_temperature_label_id is null
  and e.climate_day_end_utc <= now()
order by e.city, e.event_ticker, e.snapshot_time_utc desc;

create or replace view v_snapshots_with_settlements as
select
  e.*,
  s.settled_at_utc,
  s.winner_ticker,
  s.winner_label,
  s.settlement_temperature_f,
  s.settlement_temperature_f as kalshi_settlement_temperature_f,
  s.settlement_bracket_index,
  s.market_settlement_source,
  s.rules_primary as settlement_rules_primary,
  s.rules_secondary as settlement_rules_secondary,
  s.validation_status as settlement_validation_status,
  s.warnings as settlement_warnings,
  f.final_high_f as final_nws_high_f,
  f.product_id as final_high_product_id,
  f.issued_at_utc as final_high_issued_at_utc,
  f.validation_status as final_high_validation_status,
  f.warnings as final_high_warnings,
  twc.final_high_f as final_weather_company_high_f,
  twc.product_id as final_weather_company_product_id,
  twc.issued_at_utc as final_weather_company_issued_at_utc,
  twc.validation_status as final_weather_company_validation_status,
  twc.warnings as final_weather_company_warnings
from events e
left join settlements s on s.city = e.city and s.event_ticker = e.event_ticker
left join final_temperature_labels f
  on f.city = e.city
  and f.event_ticker = e.event_ticker
  and f.source_provider = 'nws_cli'
left join final_temperature_labels twc
  on twc.city = e.city
  and twc.event_ticker = e.event_ticker
  and twc.source_provider = 'weather_company_daily';

create or replace view v_latest_city_snapshots as
select distinct on (city, event_ticker)
  *
from events
order by city, event_ticker, snapshot_time_utc desc;

create or replace view v_collector_health as
select
  (select max(snapshot_time_utc) from collector_runs) as latest_run_utc,
  (select count(*) from v_unsettled_events) as unsettled_events,
  (select count(*) from v_events_missing_final_high) as pending_final_highs,
  (select count(*) from v_events_missing_weather_company_final_high)
    as pending_weather_company_final_highs,
  (
    select count(*)
    from provider_errors
    where snapshot_time_utc >= now() - interval '24 hours'
  ) as provider_errors_24h,
  (
    select count(distinct city)
    from events
    where snapshot_time_utc >= now() - interval '2 hours'
  ) as cities_seen_last_2h;

create or replace view v_backtest_export as
select
  e.city,
  e.event_ticker,
  e.target_date,
  e.snapshot_time_utc,
  e.snapshot_time_local,
  e.checkpoint_label,
  e.settlement_sources,
  e.rules_primary,
  e.rules_secondary,
  e.settlement_source_provider,
  e.settlement_station_id,
  w.nws_anchor_high_f,
  w.observed_high_so_far_f,
  w.settlement_observed_high_so_far_f,
  w.settlement_observed_latest_time_utc,
  w.settlement_observed_age_seconds,
  w.settlement_observed_age_hours,
  w.settlement_observed_source_count,
  w.settlement_observed_source_range_f,
  w.settlement_observed_source_stddev_f,
  w.settlement_observed_nws_delta_f,
  w.settlement_observed_sources,
  w.hrrr_projected_high_f,
  w.nbm_projected_high_f,
  w.ensemble_raw_median_high_f,
  s.winner_ticker,
  s.settlement_temperature_f,
  s.settlement_temperature_f as kalshi_settlement_temperature_f,
  s.settlement_bracket_index,
  s.market_settlement_source,
  s.rules_primary as settlement_rules_primary,
  s.rules_secondary as settlement_rules_secondary,
  f.final_high_f as final_nws_high_f,
  f.source_provider as final_high_source_provider,
  f.validation_status as final_high_validation_status,
  twc.final_high_f as final_weather_company_high_f,
  twc.source_provider as final_weather_company_source_provider,
  twc.validation_status as final_weather_company_validation_status
from events e
left join weather_snapshots w
  on w.city = e.city
  and w.event_ticker = e.event_ticker
  and w.snapshot_time_utc = e.snapshot_time_utc
left join settlements s on s.city = e.city and s.event_ticker = e.event_ticker
left join final_temperature_labels f
  on f.city = e.city
  and f.event_ticker = e.event_ticker
  and f.source_provider = 'nws_cli'
left join final_temperature_labels twc
  on twc.city = e.city
  and twc.event_ticker = e.event_ticker
  and twc.source_provider = 'weather_company_daily';
"""
