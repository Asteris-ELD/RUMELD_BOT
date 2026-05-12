-- RumBot schema for Supabase.
-- Запусти один раз в Supabase Dashboard -> SQL Editor -> New query -> вставь -> Run.

create table if not exists public.chats (
    chat_id    bigint primary key,
    title      text,
    type       text,
    lang       text not null default 'ru',
    added_at   timestamptz not null default now()
);

create table if not exists public.users (
    user_id    bigint primary key,
    username   text,
    lang       text not null default 'ru',
    updated_at timestamptz not null default now()
);

create table if not exists public.refresh_events (
    id         bigserial primary key,
    chat_id    bigint not null,
    chat_title text,
    user_id    bigint,
    username   text,
    sent_at    timestamptz not null default now()
);

alter table public.chats         enable row level security;
alter table public.users         enable row level security;
alter table public.refresh_events enable row level security;

create index if not exists refresh_events_sent_at_idx
    on public.refresh_events (sent_at desc);
create index if not exists refresh_events_chat_id_idx
    on public.refresh_events (chat_id);

-- Дневная статистика refresh-пингов по 3 сменам в Asia/Bishkek:
--   morning 07:00-15:00, evening 15:00-23:00, night 23:00-07:00.
create or replace view public.refresh_daily_stats as
with e as (
    select sent_at at time zone 'Asia/Bishkek' as local_ts
    from public.refresh_events
)
select
    local_ts::date as day,
    count(*) filter (where extract(hour from local_ts) between 7 and 14)  as shift_morning,
    count(*) filter (where extract(hour from local_ts) between 15 and 22) as shift_evening,
    count(*) filter (
        where extract(hour from local_ts) >= 23
           or extract(hour from local_ts) < 7
    ) as shift_night,
    count(*) as total
from e
group by 1
order by 1 desc;
