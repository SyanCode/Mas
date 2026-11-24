"""Auto-prompting: a short request gets questions, then a precise goal for the robot."""

from __future__ import annotations

import json

import pytest

from mas import refine
from mas.ai import Reply
from mas.db import Database


class Pool:
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def ask(self, system, messages, tools=None, max_tokens=4000, timeout=120, source="other"):
        self.seen.append((system, json.loads(messages[0]["content"]), source))
        return Reply([{"type": "text", "text": self.answers.pop(0)}])


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "mas.db")
    d.create_app("shop", name="Shop", repo="acme/shop", branch="main", type="nextjs", domain="shop.example.com")
    d.create_app("blog", name="Blog", repo="acme/blog", branch="main", type="static", domain="blog.example.com")
    return d


def test_a_vague_request_gets_questions_then_a_goal(db):
    first = json.dumps({"ready": False, "goal": "Improve the site.", "why": "two sites fit",
                        "questions": [{"question": "Which site?", "options": ["shop", "blog"]},
                                      {"question": "What matters most?", "options": ["Speed", "SEO", "Look", "x", "y"]},
                                      {"question": "  ", "options": []}, {"question": "4th", "options": []},
                                      {"question": "5th", "options": []}]})
    final = "Here it is: " + json.dumps({"ready": True, "questions": [], "why": "clear now",
                                         "goal": "Site shop. Load seo-checklist, run seo_report, fix every point."})
    pool = Pool(first, final)
    out = refine.refine(pool, db, "make my site better")
    assert out["ready"] is False and [q["question"] for q in out["questions"]] == ["Which site?", "What matters most?", "4th"]
    assert len(out["questions"][1]["options"]) == 4
    system, payload, source = pool.seen[0]
    assert source == "refine" and "Answer with JSON only" in system
    assert {s["id"] for s in payload["sites"]} == {"shop", "blog"} and "seo-checklist" in payload["skills"]
    out = refine.refine(pool, db, "make my site better",
                        answers=[{"question": "Which site?", "answer": "shop"}, {"question": "What?", "answer": " "}], rounds=1)
    assert out == {"ready": True, "questions": [], "why": "clear now",
                   "goal": "Site shop. Load seo-checklist, run seo_report, fix every point."}
    assert pool.seen[1][1]["answers"] == [{"question": "Which site?", "answer": "shop"}] and "note" in pool.seen[1][1]


def test_after_two_rounds_there_is_no_more_question(db):
    pool = Pool(json.dumps({"ready": False, "goal": "Do the SEO of shop.", "questions": [{"question": "Again?", "options": []}]}))
    out = refine.refine(pool, db, "seo", answers=[{"question": "q", "answer": "a"}], rounds=2)
    assert out["ready"] and out["questions"] == [] and out["goal"] == "Do the SEO of shop."


def test_unreadable_answers_are_errors(db):
    for text in ("Sure! I will do it.", "{not json}", json.dumps({"ready": True, "goal": ""})):
        with pytest.raises(refine.RefineError):
            refine.refine(Pool(text), db, "make it better")
    with pytest.raises(refine.RefineError):
        refine.refine(Pool(), db, "  ")


def test_the_setting_is_on_by_default(db):
    assert refine.enabled(db)
    refine.set_enabled(db, False)
    assert not refine.enabled(db) and db.get_setting(refine.SETTING) == "0"
    refine.set_enabled(db, True)
    assert refine.enabled(db)
