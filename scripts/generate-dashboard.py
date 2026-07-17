#!/usr/bin/env python3
"""
Generate docs/dashboard-data.json from all journal files.
The static docs/index.html loads this file via fetch().
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAXONOMY_LEVELS = ["kingdom", "phylum", "class", "order", "family", "genus"]

# The login of the MorphoDepot organization; a repo under it is an "org" repo (S3-backed,
# governed) as opposed to a personal repo (GitHub release asset).
ORG_LOGIN = "MorphoDepot"

# Orgs that exist purely for the module's self-test / Reload-and-Test runs — everything under
# them is a test artifact (the self-test publishes to MorphoDepotTesting; MorphoDepotTest holds
# hand-run tests).  Anything owned by one of these is auto-excluded, name notwithstanding.
TEST_ORGS = frozenset({"MorphoDepotTesting", "MorphoDepotTest"})

# Specific repos to exclude that carry no give-away word in the name (e.g. the SlicerMorph test
# account's scratch repo).  Matched case-insensitively on the full owner/name.
EXCLUDED_REPOS = frozenset({"amm554/non-member"})

# Duplicate-group categories, ordered by curation priority (lower = more urgent).
DUP_CATEGORY_PRIORITY = {"org-org": 0, "promotion": 1, "cross-owner": 2, "same-owner": 3}


def accession_answer(accession, key):
    """Accession values are [question, answer] pairs; return just the answer."""
    v = accession.get(key)
    if isinstance(v, list) and len(v) >= 2:
        return v[1]
    return v


def _name_tokens(name):
    """Split a repo name into lowercase word tokens, breaking on separators (``_ - .`` etc.),
    digit boundaries, AND camelCase humps — so ``CDHumanTest`` -> ['cd', 'human', 'test'] and
    ``MRI_test_head`` -> ['mri', 'test', 'head'].  This lets us match a whole word 'test'/'demo'
    without also flagging species like 'Testudo' (a tortoise) or 'Demospongiae' (a sponge)."""
    tokens = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        tokens += re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+", part)
    return [t.lower() for t in tokens]


# Whole-name-token words that mark a repo as a throwaway (test / demo / teaching / conference
# artifact): never showcase it, and don't count it as a dataset repo.  Matched as a WORD (via
# _name_tokens), so real taxa/collections are safe: 'Testudo' (tortoise), 'Demospongiae' (sponge),
# and the plural 'Museum_samples' all keep their real status while 'MRhead_sample' does not.  Note
# 'sicb' targets the SICB-conference workshop repos ("Society for Integrative & Comparative Biology").
_EXCLUDED_NAME_WORDS = frozenset({"test", "demo", "sample", "sicb", "workshop", "practice"})


def is_test_repo(name_with_owner):
    """True for throwaway repos that should never showcase and don't count as dataset repos:
      * anything under a known testing org (MorphoDepotTesting / MorphoDepotTest),
      * an explicitly listed one-off (EXCLUDED_REPOS), or
      * a name containing the whole word 'test', 'demo', 'sample', 'sicb', 'workshop', or
        'practice' (case-insensitive, word-boundary aware).
    Catches the module's self-test names (`test-repo-<n>`, `test-<genus>-<species>-<n>`) plus
    user-named try-outs / teaching / conference repos (`MRI_test_head`, `CDHumanTest`,
    `Demo_HumanHead`, `SICB_sample`, `KLF_SICB_2026`, `Dasyuridae_Workshop`, `MRhead_sample`)."""
    owner, _, name = name_with_owner.partition("/")
    if owner in TEST_ORGS:
        return True
    if name_with_owner.lower() in EXCLUDED_REPOS:
        return True
    return not _EXCLUDED_NAME_WORDS.isdisjoint(_name_tokens(name))


def is_ephemeral_repo(accession):
    """True for 'ephemeral' repos — the accession's repoType lifespan is Short-term
    (classroom/disposable), not Archival."""
    rt = accession_answer(accession, "repoType") or ""
    return isinstance(rt, str) and rt.startswith("Short-term")


def categorize_duplicate(repos):
    """Classify a set of repos that share one source-volume checksum.

    promotion   = the same volume in both a personal repo and the org (personal->org).
    org-org     = two or more org repos (storage + provenance waste; highest priority).
    same-owner  = multiple personal repos by one owner (usually benign: classroom reuse).
    cross-owner = multiple personal repos across owners (possible attribution/reuse issue).
    """
    org = [r for r in repos if r["isOrg"]]
    personal = [r for r in repos if not r["isOrg"]]
    if org and personal:
        return "promotion"
    if len(org) > 1:
        return "org-org"
    if len({r["owner"] for r in repos}) == 1:
        return "same-owner"
    return "cross-owner"


def build_duplicate_report(checksum_to_repos):
    """Group repos by checksum; return (duplicate_groups, checksum_index).

    duplicate_groups: only checksums shared by >1 repo, categorized and priority-sorted.
    checksum_index:   every checksum -> [nameWithOwner, ...] (the published lookup index
                      consumed by the stage/publish-time duplicate warning).
    """
    duplicate_groups = []
    checksum_index = {}
    for checksum, repos in checksum_to_repos.items():
        # De-dup repos within a checksum (a repo should appear once); keep insertion order.
        seen, unique = set(), []
        for r in repos:
            if r["nameWithOwner"] not in seen:
                seen.add(r["nameWithOwner"])
                unique.append(r)
        checksum_index[checksum] = [r["nameWithOwner"] for r in unique]
        if len(unique) > 1:
            category = categorize_duplicate(unique)
            duplicate_groups.append({
                "checksum": checksum,
                "category": category,
                "repos": [{"nameWithOwner": r["nameWithOwner"], "isOrg": r["isOrg"],
                           "species": r["species"], "isTest": r["isTest"],
                           "isEphemeral": r["isEphemeral"]} for r in unique],
            })
    duplicate_groups.sort(key=lambda g: (DUP_CATEGORY_PRIORITY.get(g["category"], 9),
                                         g["checksum"]))
    return duplicate_groups, checksum_index


def build_collections(collection_journals, known):
    """Resolve each collection's member references against the known dataset repos and emit
    the dashboard `collections[]` entries.

    `known` maps nameWithOwner -> repo dict (from repos_list). A member reference that doesn't
    resolve becomes a warning rather than a member; short-term members and a missing title are
    also surfaced as warnings so a curator/admin sees a plain report.
    """
    collections = []
    for j in collection_journals:
        nwo = j.get("nameWithOwner", "")
        _, _, name = nwo.partition("/")
        block = j.get("collection") or {}
        title = block.get("title") or ""
        members, warnings, seen = [], [], set()

        if not title:
            warnings.append("Missing collection title (first line of the README).")
        elif "github.com" in title or title.lower().startswith(("http://", "https://")):
            warnings.append(
                "The first README line looks like a URL, not a title — put a collection title first.")

        for ref in block.get("memberRefs") or []:
            if ref in seen:
                continue
            seen.add(ref)
            repo = known.get(ref)
            if repo is None:
                warnings.append(f"Unresolved member (not a known MorphoDepot repo): {ref}")
                continue
            members.append(ref)
            if repo.get("isEphemeral"):
                warnings.append(f"Member is a short-term repo and may not persist: {ref}")

        if len(members) < 2:
            warnings.append(
                f"{len(members)} resolved member(s); collections are expected to list at least 2.")

        collections.append({
            "slug": name,
            "nameWithOwner": nwo,
            "title": title or name,
            "description": block.get("description", ""),
            "curator": j.get("curator"),
            "members": members,
            "warnings": warnings,
        })

    collections.sort(key=lambda c: c["title"].lower())
    return collections


def main():
    journals_dir = Path("journals")
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)

    total_open_issues = 0
    total_open_prs = 0
    taxonomy = {level: {} for level in TAXONOMY_LEVELS}
    activity = {"last_day": 0, "last_week": 0, "last_month": 0, "last_year": 0}
    repos_list = []
    collection_journals = []
    checksum_to_repos = {}  # source-volume sha256 -> [repo meta, ...]

    for journal_path in sorted(journals_dir.glob("*.json")):
        try:
            with open(journal_path) as f:
                j = json.load(f)
        except Exception as e:
            print(f"  Skipping {journal_path}: {e}")
            continue

        nwo = j.get("nameWithOwner", "")

        # Collection repos ("repo of repos") are aggregated separately (build_collections) and
        # excluded from the dataset stats / taxonomy / duplicate-volume analysis below.
        if isinstance(j.get("collection"), dict):
            collection_journals.append(j)
            continue

        open_issues_count = len(j.get("openIssues", []))
        open_prs_count = len(j.get("openPRs", []))
        pushed_at_str = j.get("pushedAt", "")

        total_open_issues += open_issues_count
        total_open_prs += open_prs_count

        # Activity windows
        if pushed_at_str:
            try:
                pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
                age = now - pushed_at
                if age <= timedelta(days=1):
                    activity["last_day"] += 1
                if age <= timedelta(weeks=1):
                    activity["last_week"] += 1
                if age <= timedelta(days=30):
                    activity["last_month"] += 1
                if age <= timedelta(days=365):
                    activity["last_year"] += 1
            except Exception:
                pass

        # Taxonomy from accession
        accession = j.get("accession", {})
        for level in TAXONOMY_LEVELS:
            val = accession.get(level) or "Unknown"
            taxonomy[level][val] = taxonomy[level].get(val, 0) + 1

        is_test = is_test_repo(nwo)
        is_ephemeral = is_ephemeral_repo(accession)
        # Repository tier is OWNER-based, not the self-declared accession repoType: "Archival" iff the
        # repo is owned by the MorphoDepot org (the gated, reviewed home), else "Personal".  This is
        # authoritative -- a personal-account repo that self-declared "Archival" (isEphemeral stays
        # False) is still correctly Personal here.
        is_archival = nwo.split("/", 1)[0] == ORG_LOGIN

        repos_list.append({
            "nameWithOwner": nwo,
            "pushedAt": pushed_at_str,
            "journalUpdatedAt": j.get("journalUpdatedAt", ""),
            "openIssues": open_issues_count,
            "openPRs": open_prs_count,
            "screenshotCount": j.get("screenshotCount", 0),
            "screenshotCaptions": j.get("screenshotCaptions", []),
            "accession": accession,
            "isArchival": is_archival,
            "isTest": is_test,
            "isEphemeral": is_ephemeral,
        })

        # Collect source-volume checksums for duplicate detection (schema v2+).
        checksum = j.get("sourceVolumeChecksum")
        if checksum and nwo:
            owner = nwo.split("/", 1)[0]
            checksum_to_repos.setdefault(checksum, []).append({
                "nameWithOwner": nwo,
                "owner": owner,
                "isOrg": owner == ORG_LOGIN,
                "species": accession_answer(accession, "species") or "Unknown",
                "isTest": is_test,
                "isEphemeral": is_ephemeral,
            })

    # Sort by most recently pushed
    repos_list.sort(key=lambda r: r.get("pushedAt", ""), reverse=True)

    duplicate_groups, checksum_index = build_duplicate_report(checksum_to_repos)
    known_repos = {r["nameWithOwner"]: r for r in repos_list}
    collections = build_collections(collection_journals, known_repos)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    data = {
        "generatedAt": generated_at,
        "totalRepos": len(repos_list),
        "totalOpenIssues": total_open_issues,
        "totalOpenPRs": total_open_prs,
        "taxonomy": taxonomy,
        "activity": activity,
        "repos": repos_list,
        "duplicateVolumes": duplicate_groups,
        "collections": collections,
    }

    out_path = docs_dir / "dashboard-data.json"
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print(f"Wrote {out_path} ({len(repos_list)} repo(s), "
          f"{len(duplicate_groups)} duplicate group(s), {len(collections)} collection(s))")

    # Published checksum -> [repo] index, consumed by the extension's stage/publish-time
    # duplicate-volume warning. Every known checksum (not just duplicates) is listed so any
    # incoming volume can be looked up.
    index_path = docs_dir / "volume-checksums.json"
    with open(index_path, "w") as f:
        json.dump({
            "generatedAt": generated_at,
            "schemaVersion": 2,
            "checksums": checksum_index,
        }, f, indent=2)
        f.write("\n")

    print(f"Wrote {index_path} ({len(checksum_index)} checksum(s))")


if __name__ == "__main__":
    main()
