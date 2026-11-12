"""CMS back-office: drafts and scheduling, media library, dashboard and activity."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_cms import PNG, SCHEMA, env, login  # noqa: F401  (env is a fixture)

from mas.cms import CmsError, clean_status, parse_schema

BASE = "/api/cms/sites/site/collections/articles/entries"


def with_drafts(env_):
    client, db, gh = env_
    schema = copy.deepcopy(SCHEMA)
    schema["collections"][0]["drafts"] = True
    gh.files["mas.cms.json"] = json.dumps(schema).encode()
    client.app.state.cms._schemas.clear()
    return client, db, gh, {"x-csrf-token": login(client)}


def in_minutes(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M")


def test_drafts_option_is_validated():
    c = parse_schema(json.dumps({"collections": [{**SCHEMA["collections"][0], "drafts": True}]}))["collections"][0]
    assert c["drafts"] is True
    with pytest.raises(CmsError):
        parse_schema(json.dumps({"collections": [{"id": "a", "storage": "supabase", "drafts": True,
                                                  "fields": [{"key": "title"}]}]}))
    assert clean_status(c, None, None) == ("published", None)
    with pytest.raises(CmsError):
        clean_status(c, "scheduled", in_minutes(-5))
    with pytest.raises(CmsError):
        clean_status(c, "scheduled", "tomorrow")
    plain = parse_schema(json.dumps(SCHEMA))["collections"][0]
    with pytest.raises(CmsError):
        clean_status(plain, "draft", None)


def test_draft_publish_and_schedule(env):
    client, db, gh, h = with_drafts(env)
    r = client.post(BASE, json={"values": {"title": "Soon"}, "status": "draft"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "draft"
    saved = json.loads(gh.files["content/articles/soon.json"])
    assert saved["status"] == "draft"
    assert json.loads(gh.files["content/articles/index.json"])[0]["status"] == "draft"
    assert client.get(f"{BASE}/soon").json()["status"] == "draft"
    listed = client.get("/api/cms/sites/site").json()["collections"][0]["entries"]
    assert listed[0]["status"] == "draft"

    # no date in the past, a future one is kept in the entry and in the schedule
    assert client.post(f"{BASE}/soon/status", json={"status": "scheduled", "publish_at": in_minutes(-1)},
                       headers=h).status_code == 400
    when = in_minutes(60)
    r = client.post(f"{BASE}/soon/status", json={"status": "scheduled", "publish_at": when}, headers=h)
    assert r.status_code == 200, r.text
    assert json.loads(gh.files["content/articles/soon.json"])["publish_at"] == when
    assert [x["slug"] for x in db.cms_scheduled()] == ["soon"]
    assert client.get(f"{BASE}/soon").json()["publish_at"] == when
    assert "Soon" in gh.commits[-1][0] and gh.commits[-1][0].startswith("cms(articles): schedule")

    # the scheduler publishes it once the date is reached, not before
    cms = client.app.state.cms
    assert cms.run_due(datetime.now(timezone.utc)) == []
    done = cms.run_due(datetime.now(timezone.utc) + timedelta(hours=2))
    assert [d["slug"] for d in done] == ["soon"]
    final = json.loads(gh.files["content/articles/soon.json"])
    assert final["status"] == "published" and "publish_at" not in final
    assert db.cms_scheduled() == []
    assert any(e["actor"] == "scheduler" for e in db.history(None, 20))

    # an editing round trip keeps the status; a collection without drafts refuses one
    entry = client.get(f"{BASE}/soon").json()
    r = client.put(f"{BASE}/soon", json={"values": {**entry["values"], "title": "Sooner"}, "sha": entry["sha"],
                                         "status": "draft"}, headers=h)
    assert r.status_code == 200 and json.loads(gh.files["content/articles/soon.json"])["status"] == "draft"
    assert client.delete(f"{BASE}/soon", params={"sha": client.get(f"{BASE}/soon").json()["sha"]},
                         headers=h).status_code == 200


def test_status_refused_without_drafts(env):
    client, db, gh = env
    h = {"x-csrf-token": login(client)}
    assert client.post(BASE, json={"values": {"title": "X"}, "status": "draft"}, headers=h).status_code == 400
    assert client.post(BASE, json={"values": {"title": "X"}}, headers=h).status_code == 200
    assert client.post(f"{BASE}/x/status", json={"status": "draft"}, headers=h).status_code == 400


def test_media_library(env):
    client, db, gh, h = with_drafts(env)
    gh.files["public/uploads/old-1.png"] = PNG
    gh.files["public/uploads/notes.txt"] = b"x"
    r = client.post("/api/cms/sites/site/media?folder=public/uploads&name=Logo.png", content=PNG, headers=h)
    assert r.status_code == 200, r.text
    value = r.json()["value"]
    assert value.startswith("/uploads/logo-") and f"public{value}" in gh.files
    assert client.post("/api/cms/sites/site/media?folder=../etc&name=a.png", content=PNG, headers=h).status_code == 400
    assert client.post("/api/cms/sites/site/media?folder=public/uploads", content=b"<svg/>", headers=h).status_code == 400

    media = client.get("/api/cms/sites/site/media").json()
    assert [i["name"] for i in media["items"]] == sorted(["old-1.png", value.rsplit("/", 1)[1]])
    assert all(i["used_in"] == [] for i in media["items"])

    # an image used by the content cannot be deleted
    client.post(BASE, json={"values": {"title": "With cover", "cover": "/uploads/old-1.png"}}, headers=h)
    media = client.get("/api/cms/sites/site/media").json()
    assert next(i for i in media["items"] if i["name"] == "old-1.png")["used_in"] == ["Articles"]
    r = client.post("/api/cms/sites/site/media/delete", json={"value": "/uploads/old-1.png"}, headers=h)
    assert r.status_code == 400 and "Articles" in r.json()["detail"]
    assert client.post("/api/cms/sites/site/media/delete", json={"value": "/elsewhere/a.png"}, headers=h).status_code == 400
    assert client.post("/api/cms/sites/site/media/delete", json={"value": value}, headers=h).status_code == 200
    assert f"public{value}" not in gh.files


def test_dashboard_overview_and_activity(env):
    client, db, gh, h = with_drafts(env)
    client.post(BASE, json={"values": {"title": "One"}, "status": "published"}, headers=h)
    client.post(BASE, json={"values": {"title": "Two"}, "status": "draft"}, headers=h)
    client.post(BASE, json={"values": {"title": "Three"}, "status": "scheduled", "publish_at": in_minutes(30)},
                headers=h)

    over = client.get("/api/cms/sites/site/overview").json()
    articles = over["collections"][0]
    assert (articles["published"], articles["draft"], articles["scheduled"], articles["total"]) == (1, 1, 1, 3)
    assert [p["id"] for p in over["pages"]] == ["home"]

    dash = client.get("/api/cms/dashboard").json()
    assert sorted(s["id"] for s in dash["sites"]) == ["other", "site"]  # an administrator sees every site
    assert dash["scheduled"] == 1 and dash["activity"] and dash["activity"][0]["site"] == "Client site"
    assert set(dash["totals"]) == {"pageviews", "visitors", "requests"}

    log = client.get("/api/cms/activity", params={"app_id": "site"}).json()
    assert {e["action"] for e in log} == {"cms-save"} and log[0]["actor"] == "admin"
    assert client.get("/api/cms/activity", params={"app_id": "site", "limit": 1}).json().__len__() == 1


def test_editor_sees_only_own_sites_in_backoffice(env):
    client, db, gh, h = with_drafts(env)
    r = client.post("/api/users", json={"username": "client"}, headers=h).json()
    client.put(f"/api/users/{r['id']}/apps", json={"apps": ["site"]}, headers=h)
    client.post("/api/logout", headers=h)
    eh = {"x-csrf-token": login(client, "client", r["password"])}
    dash = client.get("/api/cms/dashboard").json()
    assert [s["id"] for s in dash["sites"]] == ["site"] and dash["editors"] is None
    assert client.get("/api/cms/sites/other/media").status_code == 404
    assert client.get("/api/cms/sites/other/overview").status_code == 404
    assert client.get("/api/cms/activity", params={"app_id": "other"}).json() == []
    assert client.post("/api/cms/sites/other/media/delete", json={"value": "/uploads/a.png"}, headers=eh).status_code == 404
