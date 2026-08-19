-- BarMate schema for Supabase.
--
-- Run once, in the Supabase SQL Editor, before scripts/seed_supabase.py.
-- Safe to re-run: every object is dropped first, so re-seeding from a clean
-- state is one paste rather than a manual clear-out.
--
-- Two rules this schema exists to enforce:
--
--   1. Nothing the ground truth knows appears here. The generated ledger
--      carries an incident_id on the chat messages that correspond to planted
--      incidents; that column is deliberately absent below. An agent that can
--      filter on it lists every incident without reading anything, which is
--      what the evaluation is supposed to measure.
--
--   2. Dates are dates, not text. Every tool filters by date range, and a
--      range scan over text works by luck of ISO ordering right up until it
--      does not.

begin;

drop table if exists sales cascade;
drop table if exists inventory_counts cascade;
drop table if exists orders cascade;
drop table if exists reservations cascade;
drop table if exists staff_schedule cascade;
drop table if exists shift_reports cascade;
drop table if exists whatsapp_messages cascade;
drop table if exists broadcasts cascade;
drop table if exists weather cascade;
drop table if exists holidays cascade;
drop table if exists cocktail_recipes cascade;
drop table if exists cocktails cascade;
drop table if exists knowledge cascade;
drop table if exists products cascade;
drop table if exists suppliers cascade;
drop table if exists staff cascade;

-- ---------------------------------------------------------------- reference

create table suppliers (
  supplier_id     text primary key,
  name            text not null,
  delivery_days   text,
  min_order_rule  text,
  min_order_qty   numeric,
  categories      text,
  contact_note    text
);

create table products (
  product_id    text primary key,
  name          text not null,
  name_he       text,
  category      text not null,
  unit          text,
  volume_ml     numeric,
  unit_cost     numeric,
  unit_price    numeric,
  station       text,
  supplier_id   text references suppliers(supplier_id),
  case_size     numeric,
  is_draught    boolean not null default false,
  safety_stock  numeric
);
create index on products (category);
create index on products (lower(name));

create table staff (
  staff_id    text primary key,
  name_he     text,
  name_en     text,
  role        text,
  authority   text,
  station     text,
  experience  text
);

create table cocktails (
  cocktail_id  text primary key,
  name         text not null,
  name_he      text,
  price        numeric
);

-- Cocktail draw has to be counted when sizing stock, or Prosecco, Aperol,
-- Bacardi and tonic get starved by reordering from direct sales alone.
create table cocktail_recipes (
  cocktail_id            text not null references cocktails(cocktail_id),
  ingredient_product_id  text not null references products(product_id),
  quantity_ml            numeric,
  quantity_per_cocktail  numeric,
  primary key (cocktail_id, ingredient_product_id)
);
create index on cocktail_recipes (ingredient_product_id);

-- ------------------------------------------------------------------- ledger

create table sales (
  sale_id                     text primary key,
  date                        date not null,
  item_type                   text,
  item_id                     text,
  item_name                   text,
  category                    text,
  units_sold                  numeric,
  lost_sales_due_to_stockout  numeric,
  revenue                     numeric
);
create index on sales (date);
create index on sales (item_id, date);

create table inventory_counts (
  count_id        text primary key,
  date            date not null,
  product_id      text not null references products(product_id),
  product_name    text,
  reported_stock  numeric,
  counted_by      text,
  station         text
);
create index on inventory_counts (product_id, date desc);

-- The books believe the invoice, not the delivery. When a delivery arrives
-- short, that belief is the discrepancy, so quantity and actual_delivery_date
-- are both kept and are not the same claim.
create table orders (
  order_id                text primary key,
  order_date              date not null,
  product_id              text not null references products(product_id),
  product_name            text,
  quantity                numeric,
  expected_delivery_date  date,
  actual_delivery_date    date,
  status                  text,
  supplier_id             text references suppliers(supplier_id),
  supplier                text
);
create index on orders (product_id, order_date);
create index on orders (status);

create table reservations (
  reservation_id    text primary key,
  date              date not null,
  time              text,
  party_size        integer,
  reservation_type  text,
  status            text
);
create index on reservations (date);

create table staff_schedule (
  schedule_id  text primary key,
  date         date not null,
  staff_id     text references staff(staff_id),
  name_en      text,
  name_he      text,
  role         text,
  station      text,
  shift_start  text,
  shift_end    text
);
create index on staff_schedule (date);

-- ------------------------------------------------------------ human sources

create table shift_reports (
  report_id     text primary key,
  date          date not null,
  submitted_at  timestamp,
  staff_id      text references staff(staff_id),
  author_name   text,
  language      text,
  raw_report    text
);
create index on shift_reports (date desc);

-- No incident_id column. See the header.
create table whatsapp_messages (
  id         bigint generated always as identity primary key,
  timestamp  timestamp not null,
  sender_id  text,
  sender     text,
  message    text
);
create index on whatsapp_messages (timestamp desc);

-- ---------------------------------------------------------------- external
-- Real data, unlike everything above. Coverage stops on 2026-06-17 and is left
-- that way on purpose: "unconfirmed beyond Wednesday" is a tested behaviour.

create table broadcasts (
  id                      bigint generated always as identity primary key,
  broadcast_date          date not null,
  day                     text,
  broadcast_time          text,
  channel                 text,
  sport_type              text,
  event_name              text,
  competition_or_context  text,
  is_live                 boolean,
  source_url              text
);
create index on broadcasts (broadcast_date);

create table weather (
  date                date primary key,
  temperature_2m_max  numeric,
  temperature_2m_min  numeric,
  precipitation_sum   numeric,
  wind_speed_10m_max  numeric,
  source              text
);

create table holidays (
  date      date not null,
  title     text not null,
  hebrew    text,
  category  text,
  yomtov    boolean,
  source    text,
  primary key (date, title)
);

-- --------------------------------------------------------------- knowledge
-- The operations manual. Text lives here; the vectors live in Pinecone, which
-- returns doc_id and passage together so a hit needs no second round trip.

create table knowledge (
  doc_id  text primary key,
  title   text not null,
  text    text not null
);

commit;
