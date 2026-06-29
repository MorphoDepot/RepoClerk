#!/usr/bin/env python3
"""Unit tests for the collections logic: drain.parse_collection_readme and
generate-dashboard.build_collections.

Run: python3 scripts/test_collections.py   (no deps, no network)
"""
import importlib.util
import re
from pathlib import Path

# generate-dashboard.py has a __main__ guard, so importlib-load it directly.
_spec = importlib.util.spec_from_file_location(
    "gen_dashboard", Path(__file__).with_name("generate-dashboard.py"))
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)

# drain.py ends with a bare main() call, so load it with that call stripped (no network run).
_drain_src = Path(__file__).with_name("drain.py").read_text()
_drain_src = re.sub(r'^main\(\)\s*$', '', _drain_src, flags=re.M)
_drain_ns = {}
exec(compile(_drain_src, "drain.py", "exec"), _drain_ns)
parse_collection_readme = _drain_ns["parse_collection_readme"]


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name


def known_repo(nwo, isEphemeral=False):
    return {"nameWithOwner": nwo, "isEphemeral": isEphemeral}


def collection_journal(nwo, title="T", desc="", curator="me", refs=()):
    return {"nameWithOwner": nwo, "curator": curator,
            "collection": {"title": title, "description": desc, "memberRefs": list(refs)}}


def test_parse_readme():
    readme = (
        "# Snakes of Texas\n\n"
        "Squamate reptiles collected in Texas.\n\n"
        "- https://github.com/MorphoDepot/Crotalus_atrox\n"
        "- someLab/Pantherophis_obsoletus\n"
        "See also github.com/MorphoDepot/Crotalus_atrox again, and prose with and/or in it.\n"
    )
    out = parse_collection_readme(readme, "MorphoDepot/snakes-of-texas")
    check("title is first line, leading # stripped", out["title"] == "Snakes of Texas")
    check("description is first prose paragraph",
          out["description"] == "Squamate reptiles collected in Texas.")
    check("harvests URL + bare-line refs",
          out["memberRefs"] == ["MorphoDepot/Crotalus_atrox", "someLab/Pantherophis_obsoletus"])
    check("de-dups a repeated ref", out["memberRefs"].count("MorphoDepot/Crotalus_atrox") == 1)
    check("prose 'and/or' is not harvested", "and/or" not in out["memberRefs"])


def test_parse_excludes_self_and_strips_git():
    readme = ("# Coll\n"
              "https://github.com/MorphoDepot/self.git\n"
              "https://github.com/owner/repo.git\n")
    out = parse_collection_readme(readme, "MorphoDepot/self")
    check("self reference excluded", "MorphoDepot/self" not in out["memberRefs"])
    check(".git suffix stripped", "owner/repo" in out["memberRefs"])


def test_build_collections_resolution():
    known = {
        "MorphoDepot/a": known_repo("MorphoDepot/a"),
        "MorphoDepot/b": known_repo("MorphoDepot/b"),
        "alice/eph": known_repo("alice/eph", isEphemeral=True),
    }
    cj = [collection_journal("MorphoDepot/snakes", title="Snakes", refs=[
        "MorphoDepot/a", "MorphoDepot/b", "alice/eph", "ghost/missing", "MorphoDepot/a"])]
    c = gd.build_collections(cj, known)[0]
    check("slug from repo name", c["slug"] == "snakes")
    check("title carried", c["title"] == "Snakes")
    check("curator carried", c["curator"] == "me")
    check("resolved members only, de-duped, order preserved",
          c["members"] == ["MorphoDepot/a", "MorphoDepot/b", "alice/eph"])
    warn = " ".join(c["warnings"])
    check("unresolved member warned", "ghost/missing" in warn)
    check("ephemeral member warned", "short-term" in warn and "alice/eph" in warn)


def test_build_collections_min_members_and_title():
    known = {"MorphoDepot/a": known_repo("MorphoDepot/a")}
    c = gd.build_collections(
        [collection_journal("MorphoDepot/solo", title="", refs=["MorphoDepot/a"])], known)[0]
    warn = " ".join(c["warnings"])
    check("missing title warned", "Missing collection title" in warn)
    check("fewer than 2 members warned", "at least 2" in warn)
    check("title falls back to slug when empty", c["title"] == "solo")


def test_build_collections_url_title_flagged():
    known = {"MorphoDepot/a": known_repo("MorphoDepot/a"), "MorphoDepot/b": known_repo("MorphoDepot/b")}
    c = gd.build_collections(
        [collection_journal("MorphoDepot/x", title="https://github.com/owner/a",
                            refs=["MorphoDepot/a", "MorphoDepot/b"])], known)[0]
    check("URL-as-title flagged", any("looks like a URL" in w for w in c["warnings"]))


def test_build_collections_sorted_case_insensitive():
    known = {"MorphoDepot/a": known_repo("MorphoDepot/a"), "MorphoDepot/b": known_repo("MorphoDepot/b")}
    cj = [
        collection_journal("MorphoDepot/z", title="Zebra", refs=["MorphoDepot/a", "MorphoDepot/b"]),
        collection_journal("MorphoDepot/a2", title="alpha", refs=["MorphoDepot/a", "MorphoDepot/b"]),
    ]
    cols = gd.build_collections(cj, known)
    check("collections sorted by title (case-insensitive)",
          [c["title"] for c in cols] == ["alpha", "Zebra"])


if __name__ == "__main__":
    print("test_parse_readme"); test_parse_readme()
    print("test_parse_excludes_self_and_strips_git"); test_parse_excludes_self_and_strips_git()
    print("test_build_collections_resolution"); test_build_collections_resolution()
    print("test_build_collections_min_members_and_title"); test_build_collections_min_members_and_title()
    print("test_build_collections_url_title_flagged"); test_build_collections_url_title_flagged()
    print("test_build_collections_sorted_case_insensitive"); test_build_collections_sorted_case_insensitive()
    print("\nAll collections tests passed.")
