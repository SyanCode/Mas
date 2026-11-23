"""Skills (written know-how of the robot) and the polish report (signs of a generated, unfinished project)."""

from __future__ import annotations

from typer.testing import CliRunner

from mas import polish, skills
from mas.cli import app


def test_every_skill_is_a_valid_claude_code_skill():
    names = skills.names()
    assert {"seo-checklist", "not-vibecoded", "design-craft", "copywriting", "accessibility", "performance",
            "code-quality", "launch-checklist"} <= set(names)
    for name in names:
        s = skills.get(name)
        assert s["text"].startswith(f"---\nname: {name}\ndescription: ") and len(s["description"]) < 1024 and len(s["body"]) > 500
    # the 22 points of the SEO checklist are all in the skill
    body = skills.get("seo-checklist")["body"]
    assert all(f"\n{n}. **" in body for n in range(1, 23))
    assert skills.get(" SEO-Checklist ")["name"] == "seo-checklist"


def test_unknown_skills_are_refused():
    for bad in ("", "nope", "../seo-checklist", "seo-checklist.md"):
        try:
            skills.get(bad)
        except skills.SkillError as exc:
            assert "unknown skill" in str(exc)
        else:
            raise AssertionError(bad)


def test_export_writes_claude_code_skills(tmp_path):
    written = skills.export(tmp_path)
    assert len(written) == len(skills.names())
    assert (tmp_path / ".claude/skills/not-vibecoded/SKILL.md").read_text().startswith("---\nname: not-vibecoded\n")
    result = CliRunner().invoke(app, ["skills", "export", str(tmp_path / "x")])
    assert result.exit_code != 0  # not a folder
    result = CliRunner().invoke(app, ["skills", "show", "copywriting"])
    assert result.exit_code == 0 and "Banned" in result.output
    assert "launch-checklist" in CliRunner().invoke(app, ["skills"]).output


def test_polish_report_finds_the_signs_of_a_generated_site():
    files = {
        "index.html": "<html><head><title>Vite + React</title><link rel=icon href=/vite.svg></head></html>",
        "src/App.tsx": ("<h1>🚀 Unlock seamless growth</h1><p>Elevate your business ✨ 🔥 💡 🎉</p>"
                        "<p>10,000+ happy customers ★★★★★</p><a href=\"#\">Docs</a><a href=\"#\">Blog</a>"
                        "<p>Lorem ipsum dolor sit amet</p><footer>© 2023 Your Company</footer>"
                        "<div className=\"bg-gradient-to-r from-purple-500 to-pink-500\"/>"
                        "<span className=\"bg-clip-text text-transparent\"/><b className=\"bg-clip-text text-transparent\"/>"
                        "console.log(data)"),
        "README.md": "# React + Vite\nThis template provides a minimal setup",
    }
    out = polish.report(list(files), files, year=2026)
    ids = {f["id"]: f for f in out["findings"]}
    assert {"placeholder", "scaffold", "buzzwords", "fake-proof", "emoji-icons", "ai-gradients", "dead-links",
            "debug-code", "copyright-year", "favicon-missing", "404-missing", "og-image-missing"} <= set(ids)
    assert "readme-missing" not in ids and out["score"] < 30
    assert any("src/App.tsx" in w for w in ids["placeholder"]["where"])


def test_polish_report_leaves_a_finished_site_alone():
    files = {
        "app/page.tsx": "<h1>Dupont bakery</h1><p>Sourdough bread baked every morning, 7 rue Mercière, Lyon.</p>"
                        "<a href=\"/menu\">See the menu</a><footer>© {new Date().getFullYear()} Dupont</footer>",
        "README.md": "# Dupont bakery\nThe website of the bakery, deployed with Mas.",
    }
    tree = list(files) + ["app/not-found.tsx", "app/icon.svg", "app/opengraph-image.png", "node_modules/x/index.js"]
    out = polish.report(tree, files, year=2026)
    assert out["findings"] == [] and out["score"] == 100


def test_pick_reads_the_user_facing_files_first():
    tree = [{"path": p, "size": 100} for p in ("package-lock.json", "node_modules/a/index.js", "src/lib/util.ts",
                                                 "src/App.tsx", ".env", "dist/index.html", "index.html", "logo.png")]
    tree.append({"path": "src/huge.tsx", "size": 10_000_000})
    assert polish.pick(tree) == ["index.html", "src/App.tsx", "src/lib/util.ts"]
