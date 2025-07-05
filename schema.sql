
create table public.users (
    id uuid primary key,
    username text unique not null,
    hashed_password text not null,
    created_at timestamp with time zone default now()
);
create table public.games (
    id uuid primary key,
    user_id uuid references public.users(id) on delete cascade,
    secret_number text not null,
    num_digits int not null,
    turns int default 0,
    is_completed boolean default false,
    created_at timestamp with time zone default now()
);
create table public.guesses (
    id uuid primary key,
    game_id uuid references public.games(id) on delete cascade,
    guess text not null,
    numbers_correct int,
    positions_correct int,
    created_at timestamp with time zone default now()
);
create or replace view public.leaderboard as
select u.username, min(g.turns) as best_turns
from public.games g
join public.users u on u.id = g.user_id
where g.is_completed = true
group by u.username
order by best_turns asc
limit 10;
