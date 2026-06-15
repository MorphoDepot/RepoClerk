#!/usr/bin/env python3
"""Unit tests for the duplicate-volume report logic in generate-dashboard.py.

Run: python3 scripts/test_duplicates.py   (no deps, no network)
"""
import importlib.util
from pathlib import Path

# Load the hyphenated module by path.
_spec = importlib.util.spec_from_file_location(
    "gen_dashboard", Path(__file__).with_name("generate-dashboard.py"))
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)


def repo(nwo, species="Unknown"):
    owner = nwo.split("/", 1)[0]
    return {"nameWithOwner": nwo, "owner": owner, "isOrg": owner == gd.ORG_LOGIN,
            "species": species}


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name


def test_parse_checksum_file():
    # Mirrors drain.py: "SHA256:<hex>" -> bare lowercase hex.
    def parse(raw):
        val = raw.strip()
        if ":" in val:
            val = val.split(":", 1)[1].strip()
        return val.lower() or None
    check("parse strips SHA256: prefix", parse("SHA256:ABCDEF12") == "abcdef12")
    check("parse handles trailing newline", parse("SHA256:abc\n") == "abc")
    check("parse handles bare hex", parse("deadbeef") == "deadbeef")
    check("parse empty -> None", parse("   ") is None)


def test_categories():
    check("promotion (personal+org)",
          gd.categorize_duplicate([repo("alice/x"), repo("MorphoDepot/x")]) == "promotion")
    check("org-org (two org repos)",
          gd.categorize_duplicate([repo("MorphoDepot/a"), repo("MorphoDepot/b")]) == "org-org")
    check("same-owner (one personal owner)",
          gd.categorize_duplicate([repo("alice/a"), repo("alice/b")]) == "same-owner")
    check("cross-owner (different personal owners)",
          gd.categorize_duplicate([repo("alice/a"), repo("bob/b")]) == "cross-owner")
    # promotion wins even with multiple org repos present
    check("promotion precedence over org-org",
          gd.categorize_duplicate([repo("alice/x"), repo("MorphoDepot/a"),
                                   repo("MorphoDepot/b")]) == "promotion")


def test_build_report():
    data = {
        "sha-dup-personal": [repo("alice/x", "Mus musculus"), repo("bob/y", "Mus musculus")],
        "sha-promo": [repo("carol/z"), repo("MorphoDepot/z")],
        "sha-unique": [repo("dave/only")],
        "sha-orgorg": [repo("MorphoDepot/a"), repo("MorphoDepot/b")],
        # same repo listed twice must collapse to one (not a duplicate)
        "sha-self": [repo("eve/w"), repo("eve/w")],
    }
    groups, index = gd.build_duplicate_report(data)

    by_sha = {g["checksum"]: g for g in groups}
    check("unique sha not a duplicate group", "sha-unique" not in by_sha)
    check("self-dup collapses, not a group", "sha-self" not in by_sha)
    check("3 duplicate groups", len(groups) == 3)
    check("cross-owner category", by_sha["sha-dup-personal"]["category"] == "cross-owner")
    check("promotion category", by_sha["sha-promo"]["category"] == "promotion")
    check("org-org category", by_sha["sha-orgorg"]["category"] == "org-org")

    # index contains EVERY checksum (incl. unique + collapsed-self), each repo once
    check("index has all 5 checksums", len(index) == 5)
    check("self-dup index collapsed to one repo", index["sha-self"] == ["eve/w"])
    check("unique sha in index", index["sha-unique"] == ["dave/only"])

    # priority sort: org-org (0) before promotion (1) before cross-owner (2)
    check("groups priority-sorted",
          [g["category"] for g in groups] == ["org-org", "promotion", "cross-owner"])

    # species carried through
    check("species carried", by_sha["sha-dup-personal"]["repos"][0]["species"] == "Mus musculus")


if __name__ == "__main__":
    print("test_parse_checksum_file"); test_parse_checksum_file()
    print("test_categories"); test_categories()
    print("test_build_report"); test_build_report()
    print("\nAll duplicate-report tests passed.")
