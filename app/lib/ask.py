"""'Ask the data': Claude answers questions by writing read-only SQL on the warehouse (tool use).
The API key comes from the Streamlit secret ANTHROPIC_API_KEY (Anthropic Console)."""
import re

import pandas as pd

from . import db

MODEL = "claude-sonnet-5-5"
MAX_STEPS = 6
TABLES = {
    "dim_player": "one row per player (player_id like 'P003469', player_name 'SURNAME, NAME', club_code, position "
                  "G/F/C as in the fantasy game, nationality, height_cm, birth_date)",
    "dim_team": "club_code (EuroLeague code, e.g. PAN), tag (short name, e.g. PAO), team_name",
    "fct_player_game": "one row per player per game: season, round, game_key, club_code, opp_code, venue H/A, "
                       "minutes (decimal), pts, fg2m, fg2a, fg3m, fg3a, ftm, fta, oreb, dreb, reb, ast, stl, tov, blk, "
                       "blka, pf (fouls committed), fd (fouls drawn), pir, plus_minus, fp (fantasy points = PIR), "
                       "fp_win (OFFICIAL fantasy points: PIR +10% when his team wins), wl, played",
    "fct_team_game": "one row per team per game: season, round, game_key, club_code, opp_code, venue, pts, opp_pts, "
                     "poss, opp_poss, team box score and opp_* columns, wl, margin",
    "stg_schedule": "every game: season, round, game_key, game_date, start_time, home_code, away_code, played",
    "fct_injuries": "current injury report: player, club_code, status, comments, player_id",
    "fct_prices": "fantasy price history by player and round (from basketstories)",
    "fct_game_players": "the fantasy game's own list: name, position (G/F/C/HC), club_code, price (current credits), "
                        "popularity (share of ALL managers who own him, 0-1), popularity_prev, prob_playing, player_id",
    "fct_fantasy_league": "our fantasy league (OGS) standings per round: fantasy_team, owner, round, points, "
                          "total_pts, clan",
    "fct_fantasy_rosters": "line-ups of the teams of our league per round: fantasy_team, round, first_name, "
                           "last_name, position, price, pts, is_captain, starter, player_id",
    "fct_odds": "betting-market lines per game: game_key, round, home_code, away_code, home_prob, home_margin, total",
    "fct_referees": "referees of each game: season, game_key, referee, round, home_code, away_code",
    "fct_shots": "every field-goal attempt: season, game_key, club_code, player_id, x, y, zone, made, three, round",
}

SYSTEM = """You answer questions about EuroLeague basketball and EuroLeague Fantasy using ONLY the data in a DuckDB
warehouse, which you query with the run_sql tool (read-only SELECT queries).

Rules:
- Always check the data with run_sql before answering; never invent numbers. If the data does not have the answer,
  say so.
- season is the start year: 2026 = season 2026-27 (current), 2025 = 2025-26.
- Fantasy points: use fp_win (official, with the 10% win bonus) unless asked for PIR.
- Players: names are 'SURNAME, NAME' — match with ILIKE '%surname%'. Teams: join dim_team; show the tag or team name.
- Prefer per-game averages for comparisons; mention the number of games behind an average.
- Keep queries simple and add LIMIT. If a query fails, read the error and try again.
- Answer in the language of the question (Greek or English), briefly, with the key numbers; a small markdown table
  is fine for lists.
- Finish with ONE last line that starts with "SUGGESTIONS:" followed by 3 short follow-up questions (same language)
  that dig deeper into the same topic with more complex stats (per-minute, splits, trends, comparisons), separated
  by " | ". Nothing after that line.

Tables:
{tables}
"""


def schema_text() -> str:
    lines = []
    for t, desc in TABLES.items():
        try:
            cols = db.q(f"DESCRIBE {t}").column_name.tolist()
        except Exception:
            continue
        lines.append(f"- {t}: {desc}\n  columns: {', '.join(cols)}")
    try:
        latest = db.q("SELECT max(season) s, max(round) r FROM fct_player_game WHERE season = "
                      "(SELECT max(season) FROM fct_player_game)").iloc[0]
        lines.append(f"\nLatest data: season {int(latest.s)}, round {int(latest.r)}.")
    except Exception:
        pass
    return "\n".join(lines)


SAFE = re.compile(r"^\s*(with|select)\b", re.I)
BAD = re.compile(r"\b(insert|update|delete|drop|create|alter|attach|copy|pragma|install|load|export)\b", re.I)


def run_sql(sql: str, max_rows: int = 200) -> tuple[str, pd.DataFrame]:
    sql = sql.strip().rstrip(";")
    if not SAFE.match(sql) or BAD.search(sql) or ";" in sql:
        return "Only one read-only SELECT / WITH query is allowed.", pd.DataFrame()
    try:
        df = db.q(f"SELECT * FROM ({sql}) AS q LIMIT {max_rows}")
    except Exception as e:
        return f"SQL error: {e}", pd.DataFrame()
    return (df.to_csv(index=False) if len(df) else "No rows."), df


TOOLS = [{
    "name": "run_sql",
    "description": "Run ONE read-only DuckDB SELECT query on the warehouse and get the result as CSV (max 200 rows).",
    "input_schema": {"type": "object", "properties": {"sql": {"type": "string", "description": "The SELECT query"}},
                     "required": ["sql"]},
}]


def answer(client, history: list[dict], question: str, model: str = MODEL) -> tuple[str, list[dict]]:
    """history: previous turns as {'role', 'content'} (plain text). Returns (answer, queries run)."""
    msgs = [*history, {"role": "user", "content": question}]
    system = SYSTEM.format(tables=schema_text())
    queries = []
    for _ in range(MAX_STEPS):
        resp = client.messages.create(model=model, max_tokens=1500, system=system, tools=TOOLS, messages=msgs)
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return text or "(no answer)", queries
        msgs.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use":
                sql = (b.input or {}).get("sql", "")
                out, df = run_sql(sql)
                queries.append({"sql": sql, "rows": len(df), "data": df})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:20000]})
        msgs.append({"role": "user", "content": results})
    return "I could not finish the analysis in a few steps — try a more specific question.", queries


# ---------------------------------------------------------------- Google Gemini (has a free tier)
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.5-flash"


def _gemini_models(key: str) -> list[str]:
    """Available 'flash' models that can generate content, newest first (fallback when the set model is gone)."""
    import requests
    try:
        r = requests.get(f"{GEMINI_API}/models", params={"key": key, "pageSize": 200}, timeout=20)
        ms = r.json().get("models", []) if r.ok else []
    except Exception:
        return []
    names = [m["name"].split("/", 1)[-1] for m in ms
             if "generateContent" in (m.get("supportedGenerationMethods") or []) and "flash" in m.get("name", "")
             and not any(x in m.get("name", "") for x in ("image", "tts", "audio", "live", "exp", "preview"))]
    return sorted(names, reverse=True)


def answer_gemini(key: str, history: list[dict], question: str, model: str = GEMINI_MODEL) -> tuple[str, list[dict]]:
    """Same as answer() but with Gemini function calling over the REST API."""
    import requests
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in history]
    contents.append({"role": "user", "parts": [{"text": question}]})
    body_base = {
        "system_instruction": {"parts": [{"text": SYSTEM.format(tables=schema_text())}]},
        "tools": [{"function_declarations": [{
            "name": "run_sql", "description": TOOLS[0]["description"],
            "parameters": {"type": "OBJECT", "properties": {"sql": {"type": "STRING", "description": "The SELECT query"}},
                           "required": ["sql"]}}]}],
        "generation_config": {"temperature": 0.2, "max_output_tokens": 2048},
    }
    queries, tried_fallback = [], False
    for _ in range(MAX_STEPS + 1):
        r = requests.post(f"{GEMINI_API}/models/{model}:generateContent", params={"key": key},
                          json={**body_base, "contents": contents}, timeout=60)
        if r.status_code == 404 and not tried_fallback:          # model retired → take the newest flash model
            tried_fallback = True
            alts = _gemini_models(key)
            if alts:
                model = alts[0]
                continue
        if not r.ok:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        cand = (r.json().get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            text = "".join(p.get("text", "") for p in parts).strip()
            return text or "(no answer)", queries
        contents.append({"role": "model", "parts": parts})
        replies = []
        for c in calls:
            sql = (c.get("args") or {}).get("sql", "")
            out, df = run_sql(sql)
            queries.append({"sql": sql, "rows": len(df), "data": df})
            replies.append({"functionResponse": {"name": c.get("name", "run_sql"), "response": {"result": out[:20000]}}})
        contents.append({"role": "user", "parts": replies})
    return "I could not finish the analysis in a few steps — try a more specific question.", queries


# ---------------------------------------------------------------- OpenAI (Chat Completions with tools)
OPENAI_API = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-4o-mini"


def _openai_models(key: str) -> list[str]:
    """Available small chat models, newest first (fallback when the set model is not available)."""
    import requests
    try:
        r = requests.get(f"{OPENAI_API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        ids = [m["id"] for m in r.json().get("data", [])] if r.ok else []
    except Exception:
        return []
    good = [i for i in ids if i.startswith("gpt-") and "mini" in i
            and not any(x in i for x in ("audio", "realtime", "search", "transcribe", "tts", "image"))]
    return sorted(good, reverse=True)


def answer_openai(key: str, history: list[dict], question: str, model: str = OPENAI_MODEL) -> tuple[str, list[dict]]:
    """Same as answer() but with OpenAI function calling over the REST API."""
    import json
    import requests
    msgs = [{"role": "system", "content": SYSTEM.format(tables=schema_text())},
            *[{"role": m["role"], "content": m["content"]} for m in history],
            {"role": "user", "content": question}]
    tools = [{"type": "function", "function": {"name": "run_sql", "description": TOOLS[0]["description"],
                                               "parameters": TOOLS[0]["input_schema"]}}]
    head = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    queries, tried_fallback = [], False
    for _ in range(MAX_STEPS + 1):
        r = requests.post(f"{OPENAI_API}/chat/completions", headers=head, timeout=90,
                          json={"model": model, "messages": msgs, "tools": tools, "tool_choice": "auto"})
        if r.status_code in (400, 404) and "model" in r.text.lower() and not tried_fallback:
            tried_fallback = True                       # model not available on this account → newest 'mini'
            alts = _openai_models(key)
            if alts:
                model = alts[0]
                continue
        if not r.ok:
            raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
        msg = r.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip() or "(no answer)", queries
        msgs.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for c in calls:
            try:
                sql = json.loads(c["function"].get("arguments") or "{}").get("sql", "")
            except ValueError:
                sql = ""
            out, df = run_sql(sql)
            queries.append({"sql": sql, "rows": len(df), "data": df})
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out[:20000]})
    return "I could not finish the analysis in a few steps — try a more specific question.", queries


def split_suggestions(text: str) -> tuple[str, list[str]]:
    """The answer without its last 'SUGGESTIONS:' line, and those follow-up questions."""
    m = re.search(r"(?:^|\n)[ \t]*\**SUGGESTIONS\**[ \t]*:\**[ \t]*(.+)$", text or "", re.S | re.I)
    if not m:
        return text, []
    sug = [x.strip(" -*•\n") for x in m.group(1).split("|") if x.strip(" -*•\n")]
    return text[:m.start()].rstrip(), sug[:3]


# question ideas by category (complex statistics) — shown as buttons on the page
IDEAS = {
    "👤 Players": [
        "Ποιοι 10 παίκτες έχουν τους περισσότερους fantasy πόντους ανά 30 λεπτά φέτος (τουλάχιστον 3 αγώνες, 12+ λεπτά);",
        "Ποιοι guards έχουν τον καλύτερο λόγο ασίστ/λάθη φέτος με τουλάχιστον 4 ασίστ ανά αγώνα;",
        "Ποιοι παίκτες έχουν ανεβάσει περισσότερο τα fantasy points τους στα 3 τελευταία ματς σε σχέση με τον μέσο όρο τους;",
        "Ποιοι παίκτες σκοράρουν πολύ περισσότερο εντός από ό,τι εκτός έδρας;",
        "Ποιοι centers κάνουν τα περισσότερα επιθετικά ριμπάουντ ανά 30 λεπτά;",
        "Ποιοι παίκτες έχουν το υψηλότερο true shooting % με τουλάχιστον 10 σουτ ανά αγώνα;"],
    "🛡️ Teams": [
        "Ποιες ομάδες έχουν το καλύτερο net rating (επίθεση − άμυνα ανά 100 κατοχές) φέτος;",
        "Ποιες ομάδες παίζουν με τον γρηγορότερο ρυθμό (κατοχές ανά αγώνα) και πώς πάνε σε νίκες;",
        "Ποια ομάδα έχει τη μεγαλύτερη διαφορά ανάμεσα σε εντός και εκτός έδρας απόδοση;",
        "Ποιες ομάδες κερδίζουν τα περισσότερα κλειστά παιχνίδια (διαφορά ≤ 5 πόντοι);",
        "Ποιες ομάδες δίνουν τους περισσότερους fantasy πόντους στους αντίπαλους centers;"],
    "🎯 Fantasy": [
        "Ποιοι παίκτες κάτω από 8 credits είχαν τουλάχιστον 15 fantasy πόντους ανά αγώνα στα 3 τελευταία;",
        "Ποιοι παίκτες έχουν το καλύτερο fantasy points ανά credit φέτος (τουλάχιστον 3 αγώνες);",
        "Ποιοι παίκτες είναι πιο σταθεροί σε fantasy πόντους (μικρότερη διακύμανση) με μέσο όρο πάνω από 15;",
        "Ποιοι δημοφιλείς παίκτες (POP% > 20) έχουν τα λιγότερα fantasy points ανά credit;",
        "Ποιοι παίκτες έχουν χαμηλό POP% (κάτω από 5%) αλλά πάνω από 18 fantasy πόντους ανά αγώνα;"],
    "🏆 Our league": [
        "Ποια ομάδα του league μας έχει τους περισσότερους πόντους στις 3 τελευταίες αγωνιστικές;",
        "Ποιους παίκτες έχουν οι περισσότερες ομάδες του league μας και πόσους πόντους φέρνουν;",
        "Ποιοι captains έφεραν τους περισσότερους πόντους στο league μας ανά αγωνιστική;",
        "Πώς πάει το Tatsos Clan απέναντι στο Bitz Clan αγωνιστική προς αγωνιστική;"],
    "🧑‍⚖️ Referees": [
        "Ποιοι διαιτητές σφυρίζουν τα περισσότερα φάουλ ανά αγώνα και πόσες βολές δίνουν;",
        "Με ποιον διαιτητή έχει το καλύτερο και το χειρότερο ρεκόρ ο Παναθηναϊκός;",
        "Σε ποιους διαιτητές οι γηπεδούχοι κερδίζουν πιο συχνά;"],
    "🎯 Shots": [
        "Ποιοι παίκτες έχουν το καλύτερο ποσοστό στα γωνιακά τρίποντα (τουλάχιστον 10 προσπάθειες);",
        "Ποιες ομάδες σουτάρουν περισσότερο από τη μέση απόσταση και με τι ποσοστό;",
        "Ποιες ομάδες επιτρέπουν το χαμηλότερο ποσοστό στο καλάθι (at the rim) στους αντιπάλους;"],
    "🎲 Odds & games": [
        "Σε ποια παιχνίδια της επόμενης αγωνιστικής η αγορά δίνει τη μεγαλύτερη πιθανότητα νίκης;",
        "Ποια παιχνίδια αναμένεται να έχουν τους περισσότερους συνολικούς πόντους;",
        "Πώς τα πήγαν φέτος τα φαβορί της αγοράς; Πόσες φορές κέρδισε το φαβορί;"],
}
