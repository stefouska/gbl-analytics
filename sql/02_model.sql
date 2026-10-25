-- ============================================================
-- 02 MODEL: star schema
--   dim_team, dim_player, fct_game_schedule, fct_team_game,
--   fct_player_game, fct_prices, fct_injuries, fct_standings,
--   fct_fantasy_league
-- ============================================================

-- ---------- teams ----------
CREATE OR REPLACE TABLE dim_team AS
WITH sched AS (
    SELECT home_code AS club_code, any_value(home_tag) AS tag, any_value(home_name) AS team_name
    FROM stg_schedule GROUP BY 1
    UNION ALL
    SELECT away_code, any_value(away_tag), any_value(away_name) FROM stg_schedule GROUP BY 1
), seen AS (SELECT DISTINCT club_code FROM stg_boxscore)
SELECT
    c.club_code,
    coalesce(r.tag, any_value(s.tag), c.club_code)            AS tag,
    coalesce(r.team_name, any_value(s.team_name), c.club_code) AS team_name,
    r.country,
    r.prices_code
FROM (SELECT club_code FROM seen UNION SELECT club_code FROM sched UNION SELECT club_code FROM ref_teams) c
LEFT JOIN ref_teams r USING (club_code)
LEFT JOIN sched s USING (club_code)
WHERE c.club_code IS NOT NULL
GROUP BY c.club_code, r.tag, r.team_name, r.country, r.prices_code;

-- ---------- team box score per game (players + team rebounds etc.) ----------
CREATE OR REPLACE TABLE fct_team_game AS
WITH t AS (
    SELECT season, phase, round, gamecode, game_key, club_code, max(is_home) AS is_home,
           greatest(200, round(sum(minutes) / 5) * 5)   AS minutes,   -- 200, 225 (OT) ...
           sum(pts) pts, sum(fg2m) fg2m, sum(fg2a) fg2a, sum(fg3m) fg3m, sum(fg3a) fg3a,
           sum(ftm) ftm, sum(fta) fta, sum(oreb) oreb, sum(dreb) dreb, sum(reb) reb,
           sum(ast) ast, sum(stl) stl, sum(tov) tov, sum(blk) blk, sum(blka) blka,
           sum(pf) pf, sum(fd) fd, sum(pir) pir
    FROM stg_boxscore
    WHERE player_id <> 'Total'
    GROUP BY ALL
), t2 AS (
    SELECT *, fg2m + fg3m AS fgm, fg2a + fg3a AS fga,
           (fg2a + fg3a) + 0.44 * fta - oreb + tov AS poss
    FROM t
)
SELECT a.*,
       CASE WHEN a.is_home = 1 THEN 'H' ELSE 'A' END           AS venue,
       b.club_code AS opp_code,
       b.pts  AS opp_pts,  b.fgm AS opp_fgm, b.fga AS opp_fga, b.fg2a AS opp_fg2a, b.fg3m AS opp_fg3m,
       b.fg3a AS opp_fg3a, b.ftm AS opp_ftm, b.fta AS opp_fta, b.oreb AS opp_oreb, b.dreb AS opp_dreb,
       b.reb  AS opp_reb,  b.ast AS opp_ast, b.stl AS opp_stl, b.tov AS opp_tov, b.blk AS opp_blk,
       b.poss AS opp_poss, b.pir AS opp_pir,
       CASE WHEN a.pts > b.pts THEN 'W' ELSE 'L' END            AS wl,
       a.pts - b.pts                                            AS margin
FROM t2 a
JOIN t2 b ON a.game_key = b.game_key AND a.club_code <> b.club_code;

-- ---------- players ----------
CREATE OR REPLACE TABLE dim_player AS
WITH last_seen AS (
    SELECT DISTINCT ON (player_id) player_id, player_name, club_code
    FROM stg_boxscore WHERE player_id NOT IN ('Team', 'Total')
    ORDER BY player_id, season DESC, round DESC
)
SELECT
    coalesce(p.player_id, l.player_id)                          AS player_id,
    coalesce(l.player_name, upper(p.player_name))               AS player_name,
    coalesce(p.club_code, l.club_code)                          AS club_code,
    coalesce(o.position,
             CASE WHEN p.position_api ILIKE 'guard%'   THEN 'G'
                  WHEN p.position_api ILIKE 'forward%' THEN 'F'
                  WHEN p.position_api ILIKE 'center%'  THEN 'C' END)   AS position,
    p.nationality,
    try_cast(p.height_cm AS DOUBLE)                             AS height_cm,
    try_cast(p.birth_date AS DATE)                              AS birth_date,
    date_diff('year', try_cast(p.birth_date AS DATE), current_date) AS age,
    name_key(coalesce(l.player_name, p.player_name))            AS nkey,          -- SURNAMENAME
    name_key(split_part(coalesce(l.player_name, ''), ',', 2) || split_part(coalesce(l.player_name, ''), ',', 1)) AS nkey_fl,
    name_key(split_part(coalesce(l.player_name, ''), ',', 1))   AS surkey
FROM last_seen l
FULL OUTER JOIN stg_players p USING (player_id)
LEFT JOIN ref_positions o ON o.player_id = coalesce(p.player_id, l.player_id);

CREATE OR REPLACE TABLE fct_player_game AS
SELECT b.season, b.phase, b.round, b.gamecode, b.game_key, b.player_id, b.club_code,
       b.is_home, t.venue, t.opp_code, t.wl, b.is_starter,
       b.minutes, (b.minutes > 0)::INT AS played,
       b.pts, b.fg2m, b.fg2a, b.fg3m, b.fg3a, b.fg2m + b.fg3m AS fgm, b.fg2a + b.fg3a AS fga,
       b.ftm, b.fta, b.oreb, b.dreb, b.reb, b.ast, b.stl, b.tov, b.blk, b.blka, b.pf, b.fd,
       b.pir, b.plus_minus,
       b.pir                                                   AS fp,       -- fantasy points = PIR
       b.pir * CASE WHEN t.wl = 'W' THEN 1 + (SELECT win_bonus FROM settings) ELSE 1 END AS fp_win,
       (b.fg2a + b.fg3a) + 0.44 * b.fta - b.oreb + b.tov        AS poss
FROM stg_boxscore b
JOIN fct_team_game t USING (game_key, club_code)
WHERE b.player_id NOT IN ('Team', 'Total');

-- ---------- schedule (one row per team per game, incl. future games) ----------
-- turn = the day of the round the game is played on (Turn 1 = first day of the round, Turn 2 = second...)
CREATE OR REPLACE TABLE fct_team_schedule AS
WITH s AS (SELECT *, dense_rank() OVER (PARTITION BY season, round ORDER BY game_date) AS turn FROM stg_schedule)
SELECT season, round, turn, game_key, game_date, start_time, played, home_code AS club_code, away_code AS opp_code,
       'H' AS venue
FROM s
UNION ALL
SELECT season, round, turn, game_key, game_date, start_time, played, away_code, home_code, 'A'
FROM s;

-- ---------- prices (matched to player_id by name, then team) ----------
CREATE OR REPLACE TABLE fct_prices AS
SELECT pr.season, pr.round, pr.price, pr.full_name, pr.prices_team,
       dp.player_id
FROM stg_prices pr
LEFT JOIN dim_team dt ON dt.prices_code = pr.prices_team
LEFT JOIN dim_player dp
       ON dp.nkey = pr.nkey OR dp.nkey_fl = pr.nkey                         -- full name
       OR (dp.surkey = pr.surkey AND dp.club_code = dt.club_code)           -- fallback: surname + team
QUALIFY row_number() OVER (PARTITION BY pr.season, pr.round, pr.nkey
                           ORDER BY (dp.nkey = pr.nkey OR dp.nkey_fl = pr.nkey) DESC NULLS LAST,
                                    (dp.club_code = dt.club_code) DESC NULLS LAST) = 1;

-- ---------- injuries (latest snapshot) ----------
CREATE OR REPLACE TABLE fct_injuries AS
-- team: from the matched player, else from the site's team name (exact name or an alias in teams.csv)
WITH rt AS (
    SELECT club_code, lower(team_name) AS nm FROM ref_teams
    UNION SELECT club_code, lower(trim(a.alias)) FROM ref_teams,
          unnest(string_split(coalesce(aliases, ''), '|')) AS a(alias) WHERE trim(a.alias) <> ''
)
SELECT i.*, dp.player_id, coalesce(rt.club_code, dp.club_code) AS club_code,
       coalesce(dp.position, CASE WHEN upper(i.position_site) IN ('PG', 'SG', 'G') THEN 'G'
                                  WHEN upper(i.position_site) IN ('SF', 'PF', 'F') THEN 'F'
                                  WHEN upper(i.position_site) = 'C' THEN 'C' END) AS position
FROM stg_injuries i
LEFT JOIN dim_player dp ON dp.nkey = i.nkey OR dp.nkey_fl = i.nkey
LEFT JOIN rt ON rt.nm = lower(trim(i.team))
WHERE i.scraped_at = (SELECT max(scraped_at) FROM stg_injuries)
QUALIFY row_number() OVER (PARTITION BY i.nkey ORDER BY dp.player_id NULLS LAST) = 1;

-- ---------- standings ----------
CREATE OR REPLACE TABLE fct_standings AS SELECT * FROM raw_standings;

-- ---------- our fantasy league ----------
CREATE OR REPLACE TABLE fct_fantasy_league AS
SELECT season::INT AS season, round::INT AS round, fantasy_team,
       try_cast(points AS DOUBLE) AS points, nullif(trim(coalesce(clan::VARCHAR, '')), '') AS clan,
       try_cast(team_id AS BIGINT) AS team_id, owner::VARCHAR AS owner,
       try_cast(total_pts AS DOUBLE) AS total_pts, try_cast(league_position AS INT) AS league_position
FROM raw_fantasy_league;

-- ---------- saved fantasy teams (last-minute check) ----------
-- fallback name matching: surname + first initial, used only when it points to exactly one player
-- (the fantasy game writes 'Nikos Rogkavopoulos', the EuroLeague 'ROGKAVOPOULOS, NIKOLAOS')
CREATE OR REPLACE TABLE dim_player_ln AS
SELECT player_id, ln, fi FROM (
    SELECT player_id, name_key(split_part(player_name, ',', 1)) AS ln,
           left(name_key(split_part(player_name, ',', 2)), 1) AS fi,
           count(*) OVER (PARTITION BY name_key(split_part(player_name, ',', 1)),
                                       left(name_key(split_part(player_name, ',', 2)), 1)) AS n
    FROM dim_player) WHERE n = 1 AND ln <> '';

-- last resort: the surname alone, when only ONE player in the EuroLeague has it
-- (the game writes 'Aleksander Balcerowski', the EuroLeague 'BALCEROWSKI, OLEK')
CREATE OR REPLACE TABLE dim_player_sur AS
SELECT player_id, ln FROM (
    SELECT player_id, name_key(split_part(player_name, ',', 1)) AS ln,
           count(*) OVER (PARTITION BY name_key(split_part(player_name, ',', 1))) AS n
    FROM dim_player) WHERE n = 1 AND length(ln) >= 4;

CREATE OR REPLACE TABLE fct_my_teams AS
WITH t AS (
    SELECT trim(owner) AS owner, trim(player) AS player_input,
           lower(trim(coalesce(role, ''))) IN ('starter', 's', 'start', 'βασικός', 'yes') AS starter,
           lower(trim(coalesce(captain, ''))) IN ('yes', 'y', 'x', '1', 'true', 'captain', 'c', 'ναι') AS captain,
           name_key(replace(player, ',', ' ')) AS k1,
           name_key(split_part(player, ',', 2) || ' ' || split_part(player, ',', 1)) AS k2,
           -- surname + first initial: 'Last, First' or 'First Last ...'
           CASE WHEN player LIKE '%,%' THEN name_key(split_part(player, ',', 1))
                ELSE name_key(regexp_replace(trim(player), '^\S+\s+', '')) END AS ln,
           CASE WHEN player LIKE '%,%' THEN left(name_key(split_part(player, ',', 2)), 1)
                ELSE left(name_key(split_part(trim(player), ' ', 1)), 1) END AS fi
    FROM raw_my_teams WHERE coalesce(trim(player), '') <> ''
)
SELECT t.owner, t.player_input, t.starter, t.captain,
       coalesce(dp.player_id, l.player_id, su.player_id) AS player_id
FROM t LEFT JOIN dim_player dp ON dp.nkey IN (t.k1, t.k2) OR dp.nkey_fl IN (t.k1, t.k2)
LEFT JOIN dim_player_ln l ON l.ln = t.ln AND l.fi = t.fi
LEFT JOIN dim_player_sur su ON su.ln = t.ln
QUALIFY row_number() OVER (PARTITION BY t.owner, t.player_input ORDER BY dp.player_id NULLS LAST) = 1;

-- ---------- injury report: what changed between the last two snapshots ----------
CREATE OR REPLACE TABLE fct_injury_changes AS
WITH h AS (
    SELECT *, name_key(player) AS nkey,
           CASE WHEN lower(coalesce(status, '')) SIMILAR TO '.*(out|will not play|season|long-term|surgery|injured).*' THEN 'OUT'
                WHEN lower(coalesce(status, '')) SIMILAR TO '.*(doubtful|uncertain|questionable|day-to-day|gtd|game-time).*' THEN 'Q'
                ELSE 'OK' END AS flag
    FROM raw_injuries_history
), snaps AS (SELECT DISTINCT scraped_at FROM h ORDER BY scraped_at DESC LIMIT 2),
cur AS (SELECT * FROM h WHERE scraped_at = (SELECT max(scraped_at) FROM snaps)),
prev AS (SELECT * FROM h WHERE scraped_at = (SELECT min(scraped_at) FROM snaps)
         AND (SELECT count(*) FROM snaps) = 2)
SELECT coalesce(c.player, p.player) AS player, coalesce(c.team, p.team) AS team, coalesce(c.nkey, p.nkey) AS nkey,
       coalesce(p.flag, 'OK') AS flag_before, coalesce(c.flag, 'OK') AS flag_now,
       p.status AS status_before, c.status AS status_now, c.comments,
       (SELECT min(scraped_at) FROM snaps) AS since, (SELECT max(scraped_at) FROM snaps) AS checked_at,
       dp.player_id
FROM cur c FULL OUTER JOIN prev p USING (nkey)
LEFT JOIN dim_player dp ON dp.nkey = coalesce(c.nkey, p.nkey) OR dp.nkey_fl = coalesce(c.nkey, p.nkey)
WHERE (SELECT count(*) FROM snaps) = 2 AND coalesce(p.flag, 'OK') <> coalesce(c.flag, 'OK')
QUALIFY row_number() OVER (PARTITION BY coalesce(c.nkey, p.nkey) ORDER BY dp.player_id NULLS LAST) = 1;

-- ---------- line-ups of the fantasy league (Dunkest), matched to our players ----------
CREATE OR REPLACE TABLE fct_fantasy_rosters AS
WITH r AS (SELECT *, name_key(coalesce(first_name, '') || ' ' || coalesce(last_name, '')) AS k1,
                  name_key(coalesce(last_name, '') || ' ' || coalesce(first_name, '')) AS k2,
                  name_key(last_name) AS ln, left(name_key(first_name), 1) AS fi
           FROM raw_fantasy_rosters)
SELECT r.season, r.round, r.matchday_id, r.fantasy_team_id, fl.fantasy_team, r.dunkest_id,
       r.first_name, r.last_name, r.position, r.club, r.price, r.pts, r.is_captain, r.court_position,
       r.starter, r.started_from_bench,
       CASE WHEN r.position = 'HC' THEN NULL ELSE coalesce(dp.player_id, l.player_id, su.player_id) END AS player_id
FROM r
LEFT JOIN dim_player dp ON r.position <> 'HC' AND (dp.nkey IN (r.k1, r.k2) OR dp.nkey_fl IN (r.k1, r.k2))
LEFT JOIN dim_player_ln l ON r.position <> 'HC' AND l.ln = r.ln AND l.fi = r.fi
LEFT JOIN dim_player_sur su ON r.position <> 'HC' AND su.ln = r.ln
LEFT JOIN (SELECT DISTINCT team_id, last_value(fantasy_team) OVER (PARTITION BY team_id ORDER BY round
                  ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS fantasy_team
           FROM fct_fantasy_league WHERE team_id IS NOT NULL) fl ON fl.team_id = r.fantasy_team_id
QUALIFY row_number() OVER (PARTITION BY r.fantasy_team_id, r.round, r.dunkest_id ORDER BY dp.player_id NULLS LAST) = 1;

-- the fantasy game's position (G/F/C) wins over the EuroLeague API: the game's rules use it
UPDATE dim_player SET position = g.position
FROM (SELECT player_id, arg_max(position, round) AS position FROM fct_fantasy_rosters
      WHERE player_id IS NOT NULL AND position IN ('G', 'F', 'C') GROUP BY 1) g
WHERE dim_player.player_id = g.player_id;

-- ---------- the game's own player list (price, % of managers who own him, availability, coaches) ----------
-- Matched by: the line-ups of our league (same game id) → full name → initial + surname + team → surname.
-- The game's team codes are translated through the players already matched in the line-ups.
CREATE OR REPLACE TABLE fct_game_players AS
WITH g AS (SELECT *, first_name || ' ' || last_name AS name, name_key(last_name) AS ln,
                  left(name_key(first_name), 1) AS fi,
                  name_key(first_name || ' ' || last_name) AS k1, name_key(last_name || ' ' || first_name) AS k2
           FROM raw_game_players),
clubmap AS (SELECT r.club AS club, arg_max(d.club_code, n) AS club_code
            FROM (SELECT club, player_id, count(*) AS n FROM fct_fantasy_rosters
                  WHERE player_id IS NOT NULL GROUP BY 1, 2) r
            JOIN dim_player d USING (player_id) GROUP BY 1),
byid AS (SELECT DISTINCT dunkest_id, player_id FROM fct_fantasy_rosters WHERE player_id IS NOT NULL),
dp AS (SELECT player_id, club_code, nkey, nkey_fl, name_key(split_part(player_name, ',', 1)) AS ln,
              left(name_key(split_part(player_name, ',', 2)), 1) AS fi FROM dim_player),
g2 AS (SELECT g.*, coalesce(cm.club_code, dt.club_code, g.club) AS club_code
       FROM g LEFT JOIN clubmap cm ON cm.club = g.club LEFT JOIN dim_team dt ON dt.tag = g.club)
SELECT g2.dunkest_id, g2.name, g2.position, g2.club, g2.club_code, g2.price, g2.popularity, g2.popularity_prev,
       g2.prob_playing, g2.is_injured, g2.is_on_fire, g2.fpt_avg, g2.round, g2.fetched_at,
       CASE WHEN g2.position = 'HC' THEN NULL ELSE coalesce(
           (SELECT b.player_id FROM byid b WHERE b.dunkest_id = g2.dunkest_id LIMIT 1),
           (SELECT dp.player_id FROM dp WHERE dp.nkey IN (g2.k1, g2.k2) OR dp.nkey_fl IN (g2.k1, g2.k2) LIMIT 1),
           (SELECT dp.player_id FROM dp WHERE dp.ln = g2.ln AND dp.fi = g2.fi AND dp.club_code = g2.club_code LIMIT 1),
           (SELECT l.player_id FROM dim_player_ln l WHERE l.ln = g2.ln AND l.fi = g2.fi LIMIT 1),
           (SELECT su.player_id FROM dim_player_sur su WHERE su.ln = g2.ln LIMIT 1)) END AS player_id
FROM g2;

UPDATE dim_player SET position = g.position
FROM (SELECT player_id, any_value(position) AS position FROM fct_game_players
      WHERE player_id IS NOT NULL AND position IN ('G', 'F', 'C') GROUP BY 1) g
WHERE dim_player.player_id = g.player_id;

-- ---------- betting-market lines (The Odds API), matched to our games ----------
CREATE OR REPLACE TABLE fct_odds AS
SELECT s.season, s.round, s.game_key, s.home_code, s.away_code, o.home_prob, o.home_margin, o.total,
       (o.total + o.home_margin) / 2 AS home_pts, (o.total - o.home_margin) / 2 AS away_pts,
       o.n_books, o.fetched_at, o.home_team AS market_home, o.away_team AS market_away
FROM raw_odds o
JOIN stg_schedule s ON s.home_code = o.home_code AND s.away_code = o.away_code
 AND abs(date_diff('day', CAST(s.game_date AS DATE), try_cast(left(o.commence_time, 10) AS DATE))) <= 2
QUALIFY row_number() OVER (PARTITION BY s.game_key ORDER BY o.fetched_at DESC) = 1;

-- ---------- shots (Game Center 'Points'): location, made/missed, zone ----------
-- Coordinates: basket at (0, 0), x across the court, y towards half court (EuroLeague units, ~cm).
CREATE OR REPLACE TABLE fct_shots AS
WITH s AS (
    SELECT Season AS season, 'E' || Season || '_' || Gamecode AS game_key, TEAM AS club_code, ID_PLAYER AS player_id,
           PLAYER AS player_name_raw, ID_ACTION AS action, COORD_X AS x, COORD_Y AS y, ZONE AS zone_api,
           coalesce(FASTBREAK, 0) > 0 AS fastbreak, coalesce(SECOND_CHANCE, 0) > 0 AS second_chance,
           coalesce(POINTS_OFF_TURNOVER, 0) > 0 AS off_turnover, MINUTE AS minute,
           ID_ACTION IN ('2FGM', '3FGM', 'LAYUPMD', 'DUNK') AS made,
           ID_ACTION IN ('3FGM', '3FGA', '3FGAB') AS three
    FROM raw_shots
    WHERE ID_ACTION IN ('2FGM', '3FGM', 'LAYUPMD', 'DUNK', '2FGA', '2FGAB', '3FGA', '3FGAB', 'LAYUPATT')
      AND COORD_X IS NOT NULL AND COORD_Y IS NOT NULL)
SELECT s.*, sqrt(x * x + y * y) AS dist,
       CASE WHEN three AND abs(x) >= 600 AND y <= 150 THEN 'Corner 3'
            WHEN three THEN 'Above the break 3'
            WHEN sqrt(x * x + y * y) <= 125 OR action IN ('DUNK', 'LAYUPMD', 'LAYUPATT') THEN 'At the rim'
            WHEN abs(x) <= 245 AND y <= 423 THEN 'Paint'
            ELSE 'Mid-range' END AS zone,
       g.round, g.opp_code, g.venue, g.wl
FROM s LEFT JOIN fct_team_game g ON g.game_key = s.game_key AND g.club_code = s.club_code;

-- ---------- referees ----------
CREATE OR REPLACE TABLE fct_referees AS
SELECT r.season, 'E' || r.season || '_' || r.gamecode AS game_key, r.slot, trim(r.referee) AS referee,
       sc.round, sc.game_date, sc.home_code, sc.away_code
FROM raw_referees r
LEFT JOIN stg_schedule sc ON sc.game_key = 'E' || r.season || '_' || r.gamecode;

-- ---------- head coach of each team in each game (Game Center header) ----------
CREATE OR REPLACE TABLE fct_game_coaches AS
WITH c AS (SELECT r.season, 'E' || r.season || '_' || r.gamecode AS game_key,
                  coalesce(nullif(r.home_code, ''), sc.home_code) AS home_code,
                  coalesce(nullif(r.away_code, ''), sc.away_code) AS away_code, r.home_coach, r.away_coach
           FROM raw_coaches r LEFT JOIN stg_schedule sc ON sc.game_key = 'E' || r.season || '_' || r.gamecode)
SELECT season, game_key, home_code AS club_code, trim(home_coach) AS coach FROM c WHERE coalesce(home_coach, '') <> ''
UNION ALL
SELECT season, game_key, away_code, trim(away_coach) FROM c WHERE coalesce(away_coach, '') <> '';
