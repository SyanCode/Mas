"""Auto-prompting: a short request becomes a precise goal for the robot before it starts.

Claude reads the request with what Mas knows (the sites, the presets, the skills) and either says it is clear and
returns a better-written goal, or asks one to three short questions (each with suggested answers) for what it cannot
find out by itself. With the answers, it writes the final goal: which site, what exactly, the skills to load, what to
check before and after, what must not change, when it is done. The person sees that goal, can edit it, and starts it.

No tool, no repository content, one request per round: the request and the answers are the person's own words; the
sites listed come from Mas's database. The answer is parsed as JSON and every field is checked and cut to size.
"""

from __future__ import annotations

import json
import re

from mas import skills as skills_mod
from mas.ai import AiError, AiPool
from mas.db import Database

SETTING = "robot_refine"  # "0": start the goals as typed; anything else: improve them first (the default)
MAX_QUESTIONS = 3
MAX_OPTIONS = 4
MAX_ROUNDS = 2  # after two rounds of answers the goal is written with what is known

SYSTEM = """\
You prepare work for the robot of Mas, an AI agent that improves websites deployed by Mas (it reads and edits their
GitHub repository, runs SEO / compliance / polish audits, deploys, checks) and looks after the server. An administrator
typed a request. Your job: turn it into the best possible goal for the robot, so that the result is what they want and
the site is as good as it can be (fast, well referenced, accessible, compliant, and not looking AI-generated).

Decide first whether the request is precise enough:
- It is NOT if an answer only the administrator can give would change what gets done: which site (when several fit),
  the exact scope (one page or the whole site), content facts the robot cannot know (prices, hours, wording, brand
  constraints), what must stay as it is, or a preference between real alternatives.
- Never ask what the robot finds out with its tools (current state, audits, files, what is broken), never ask about
  technical details it can decide well, never ask more than 3 questions, and ask nothing when the request is clear.
- Each question is short, in the language of the request, with 2 to 4 concrete suggested answers (the person can also
  type their own).

Then write the goal (always, even with questions: your best version with what is known), in the language of the
request, as a short brief the robot follows:
- the site (its id from the list) or the server;
- what to do, precisely, and why;
- the skills to load (names from the list) and the audits to run before and after;
- what must not change (identity, content, URLs…) and that facts are never invented ([TO FILL] placeholders instead);
- when it is done (a check that proves it).
Keep it under 250 words. Do not promise results; do not add work the person did not ask for, beyond what makes the
asked work good (for a new page: its SEO, mobile layout and accessibility; for a text: the copywriting rules).

Answer with JSON only, no prose around it:
{"ready": true|false, "questions": [{"question": "...", "options": ["...", "..."]}], "goal": "...", "why": "one short sentence"}
"ready" is true when there is no question.
"""


class RefineError(AiError):
    pass


def enabled(db: Database) -> bool:
    return db.get_setting(SETTING) != "0"


def set_enabled(db: Database, on: bool) -> bool:
    db.set_setting(SETTING, None if on else "0")
    return on


def context(db: Database, app_id: str) -> dict:
    sites = [{"id": a["id"], "domain": a.get("domain"), "type": a.get("type"), "repo": a.get("repo")}
             for a in db.list_apps() if a.get("repo")][:40]
    return {"sites": sites, "chosen_site": app_id or None,
            "skills": [s["name"] for s in skills_mod.catalog()]}


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RefineError("the AI did not answer with a goal: try again or start your request as it is")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise RefineError("the AI answer could not be read: try again or start your request as it is") from None
    if not isinstance(data, dict):
        raise RefineError("the AI answer could not be read")
    return data


def clean(data: dict, rounds: int) -> dict:
    goal = str(data.get("goal") or "").strip()[:4000]
    questions = []
    if rounds < MAX_ROUNDS:
        for q in (data.get("questions") or [])[:10]:
            if not isinstance(q, dict) or not str(q.get("question") or "").strip() or len(questions) >= MAX_QUESTIONS:
                continue
            options = [str(o).strip()[:120] for o in (q.get("options") or []) if str(o).strip()][:MAX_OPTIONS]
            questions.append({"question": str(q["question"]).strip()[:300], "options": options})
    if len(goal) < 5 and not questions:
        raise RefineError("the AI returned no goal: start your request as it is")
    return {"ready": not questions, "questions": questions, "goal": goal, "why": str(data.get("why") or "").strip()[:300]}


def refine(pool: AiPool, db: Database, request: str, app_id: str = "", answers: list[dict] | None = None,
           rounds: int = 0) -> dict:
    """``answers``: [{question, answer}] of the previous rounds, ``rounds`` how many rounds were answered.
    Returns {ready, questions, goal, why}; after MAX_ROUNDS rounds there is no question anymore."""
    request = (request or "").strip()
    if len(request) < 3:
        raise RefineError("describe what the robot should do")
    answers = [{"question": str(a.get("question") or "")[:300], "answer": str(a.get("answer") or "")[:500]}
               for a in (answers or []) if isinstance(a, dict) and str(a.get("answer") or "").strip()][:9]
    rounds = max(0, min(int(rounds or 0), MAX_ROUNDS))
    payload = {"request": request[:4000], "answers": answers, **context(db, app_id)}
    if answers:
        payload["note"] = ("The administrator answered your questions: write the final goal with them. Ask again only "
                           "if something essential is still missing.")
    reply = pool.ask(SYSTEM, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                     max_tokens=1500, timeout=90, source="refine")
    return clean(_json(reply.text), rounds)
