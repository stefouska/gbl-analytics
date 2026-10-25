-- ============================================================
-- 01 STAGING: clean raw sources, one consistent naming style
-- ============================================================
CREATE OR REPLACE MACRO name_key(s) AS
    regexp_replace(
        regexp_replace(upper(strip_accents(coalesce(s, ''))), '\b(JR|SR|II|III|IV)\b\.?', '', 'g'),
        '[^A-Z]', '', 'g');

CREATE OR REPLACE MACRO mmss_to_min(s) AS
    CASE WHEN s LIKE '%:%'
         THEN try_cast(split_part(s, ':', 1) AS DOUBLE) + try_cast(split_part(s, ':', 2) AS DOUBLE) / 60
         ELSE 0 END;

-- every row of the official box score (players + 'Team' + 'Total')
CREATE OR REPLACE TABLE stg_boxscore AS
SELECT
    Season::INT                                   AS season,
    Phase                                         AS phase,
    Round::INT                                    AS round,
    Gamecode::INT                                 AS gamecode,
    'E' || Season || '_' || Gamecode              AS game_key,
    trim(Player_ID)                               AS player_id,
    Player                                        AS player_name,
    Team                                          AS club_code,
    Home::INT                                     AS is_home,
    coalesce(IsStarter, 0)::INT                   AS is_starter,
    coalesce(mmss_to_min(Minutes), 0)             AS minutes,
    Points                                        AS pts,
    FieldGoalsMade2                               AS fg2m,
    FieldGoalsAttempted2                          AS fg2a,
    FieldGoalsMade3                               AS fg3m,
    FieldGoalsAttempted3                          AS fg3a,
    FreeThrowsMade                                AS ftm,
    FreeThrowsAttempted                           AS fta,
    OffensiveRebounds                             AS oreb,
    DefensiveRebounds                             AS dreb,
    TotalRebounds                                 AS reb,
    Assistances                                   AS ast,
    Steals                                        AS stl,
    Turnovers                                     AS tov,
    BlocksFavour                                  AS blk,
    BlocksAgainst                                 AS blka,
    FoulsCommited                                 AS pf,
    FoulsReceived                                 AS fd,
    Valuation                                     AS pir,          -- official EuroLeague PIR
    Plusminus                                     AS plus_minus
FROM raw_boxscores;

CREATE OR REPLACE TABLE stg_schedule AS
SELECT
    season::INT                                                     AS season,
    gameday::INT                                                    AS round,
    "round"                                                         AS phase_name,
    'E' || season || '_' || regexp_extract(gamecode, '(\d+)$', 1)   AS game_key,
    coalesce(try_strptime(date::VARCHAR, '%b %d, %Y'), try_strptime(date::VARCHAR, '%b %-d, %Y'),
             try_strptime(date::VARCHAR, '%Y-%m-%d'), try_strptime(date::VARCHAR, '%d/%m/%Y'),
             try_cast(date AS TIMESTAMP))::DATE                     AS game_date,
    startime                                                        AS start_time,
    homecode AS home_code, hometv AS home_tag, hometeam AS home_name,
    awaycode AS away_code, awaytv AS away_tag, awayteam AS away_name,
    lower(played::VARCHAR) = 'true'                                 AS played
FROM raw_schedule;

CREATE OR REPLACE TABLE stg_players AS
SELECT DISTINCT ON (player_id) *
FROM raw_players
ORDER BY player_id, season DESC;

CREATE OR REPLACE TABLE stg_prices AS
SELECT season::INT AS season, round::INT AS round, full_name, prices_team,
       price::DOUBLE AS price, name_key(full_name::VARCHAR) AS nkey,
       name_key(split_part(full_name::VARCHAR, ' ', 1)) AS surkey, scraped_at
FROM raw_prices;

CREATE OR REPLACE TABLE stg_injuries AS
WITH r AS (SELECT * FROM raw_injuries),
i AS (   -- explicit casts: an empty list arrives with untyped columns; older files lack some columns
    SELECT player::VARCHAR AS player, team::VARCHAR AS team, coalesce(status::VARCHAR, '') AS status,
           comments::VARCHAR AS comments, scraped_at::TIMESTAMP AS scraped_at,
           position_site::VARCHAR AS position_site, round_info::VARCHAR AS round_info, source::VARCHAR AS source
    FROM r
)
SELECT *,
       name_key(player) AS nkey,
       CASE
         WHEN lower(status) SIMILAR TO '.*(out|will not play|season|long-term|surgery|injured).*' THEN 'OUT'
         WHEN lower(status) SIMILAR TO '.*(doubtful|uncertain|questionable|day-to-day|gtd|game-time).*' THEN 'Q'
         ELSE 'OK' END AS availability,
       -- 0-4 scale of the Power BI column 'Injury Risk'
       CASE
         WHEN lower(status) SIMILAR TO '.*(out|will not play|season|long-term|surgery|injured).*' THEN 4
         WHEN lower(status) SIMILAR TO '.*(doubtful|uncertain|probably out).*'                 THEN 3
         WHEN lower(status) SIMILAR TO '.*(questionable|day-to-day|minor|illness).*'           THEN 2
         WHEN lower(status) SIMILAR TO '.*(expected|game-time|gtd).*'                          THEN 1
         ELSE 0 END AS injury_level
FROM i;
