#!/usr/bin/env python3
"""
Sync-all logic for RepoClerk.

1. Discovers all live morphodepot fork repos via GitHub search.
2. Creates update-request issues for repos that are missing or stale journals.
   (The drain loop in update-repo.yml handles the actual journal updates.)
3. Directly deletes journal files for repos no longer in the live set,
   and commits/pushes those deletions.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Running as `python3 scripts/sync-all.py` already puts scripts/ on sys.path, but loading
# this file by path (as test_staleness.py does) does not. Make the import work either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import SCHEMA_VERSION

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")


def run(cmd, check=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def search_topic(topic):
    """Return {nameWithOwner: {pushedAt, updatedAt, activityAt}} for repos carrying `topic`.

    All three change tokens come back in the one discovery search that was happening
    anyway.  Measured cost for the whole 80-repo fleet: 2 GraphQL points, against a
    5,000/hour budget -- the two `first: 1` connections are what keep it that cheap.

    They cover different things and none is redundant:
      pushedAt   git pushes
      updatedAt  the repository record -- description (a collection's title comes from
                 it), topics, visibility
      activityAt newest issue-or-PR update, the only cheap signal that tracks issue and
                 pull-request activity, which updatedAt does not (see drain.activity_watermark)
    """
    result = run([
        "gh", "api", "graphql",
        "--paginate",
        "--jq", ".data.search.nodes[] | {nameWithOwner, pushedAt, updatedAt, "
                "latestIssue: (.latestIssue.nodes[0].updatedAt // \"\"), "
                "latestPR: (.latestPR.nodes[0].updatedAt // \"\")}",
        "-f", f"""query=
          query($cursor: String) {{
            search(
              query: "topic:{topic} fork:true"
              type: REPOSITORY
              first: 100
              after: $cursor
            ) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                ... on Repository {{
                  nameWithOwner pushedAt updatedAt
                  latestIssue: issues(first: 1, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
                    nodes {{ updatedAt }}
                  }}
                  latestPR: pullRequests(first: 1, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
                    nodes {{ updatedAt }}
                  }}
                }}
              }}
            }}
          }}
        """,
    ])
    repos = {}
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line:
            entry = json.loads(line)
            repos[entry["nameWithOwner"]] = {
                "pushedAt": entry.get("pushedAt", ""),
                "updatedAt": entry.get("updatedAt", ""),
                # Same max() as drain.activity_watermark, so the two are comparable.
                "activityAt": max(entry.get("latestIssue") or "",
                                  entry.get("latestPR") or ""),
            }
    return repos


def staleness_reason(journal, remote):
    """Why `remote` needs re-draining, or None if the journal is current.

    `journal` is the local record (or None if there is no journal file); `remote` is
    {pushedAt, updatedAt} from the topic search.  Pure and side-effect free so it can
    be tested without network -- see test_staleness.py.

    Order matters only for the label the digest prints; any of these firing re-drains.
    pushedAt is checked first because it is the strongest signal -- new content means the
    expensive artifacts genuinely changed.

    activityAt is the one that fixes #470.  It is the newest issue-or-PR update time,
    because *neither* pushedAt nor updatedAt moves on issue and pull-request activity:
    pushedAt tracks git pushes, updatedAt tracks the repository record.  That was verified
    the hard way -- an earlier version of this fix compared updatedAt and would have
    changed nothing at all.

    Absent (None) and empty ("") are deliberately different, and conflating them is a bug
    with teeth.  None means the journal predates the field, so there is nothing to compare
    and it falls through to the schemaVersion check, which re-drains it once and writes it.
    "" means the repo genuinely has no issues or pull requests yet -- which is exactly the
    state a freshly published repo is in, so the *first* issue ever opened on it must be
    detected here.  Nothing else would catch it: creating an issue moves neither pushedAt
    nor updatedAt.  A truthiness test would skip that comparison and the repo would never
    be re-drained.
    """
    if journal is None:
        return "missing"
    if journal.get("pushedAt", "") != remote["pushedAt"]:
        return "stale"
    if journal.get("updatedAt") is not None and journal["updatedAt"] != remote["updatedAt"]:
        return "metadata"
    if journal.get("activityAt") is not None and journal["activityAt"] != remote["activityAt"]:
        return "activity"
    if journal.get("schemaVersion", 1) < SCHEMA_VERSION:
        return "schema-upgrade"
    return None


def main():
    # 1. Discover all live repos. `morphodepot` covers datasets (and collections, which also
    #    carry it); `md-collection` is searched too so a collection is discovered even if its
    #    morphodepot topic was ever dropped.
    live_repos = {}
    for topic in ("morphodepot", "md-collection"):
        live_repos.update(search_topic(topic))

    print(f"Found {len(live_repos)} live morphodepot repos")

    # 2. Read existing journal files
    journals_dir = Path("journals")
    journaled_repos = {}
    for path in journals_dir.glob("*.json"):
        stem = path.stem  # {owner}^{repo}
        if "^" not in stem:
            continue
        owner, _, repo_name = stem.partition("^")
        nwo = f"{owner}/{repo_name}"
        try:
            with open(path) as f:
                data = json.load(f)
            # None, not "", when a field is absent: a journal written before the field
            # existed must be distinguishable from one where the field is legitimately
            # empty (a repo that has never had an issue or PR). Collapsing the two makes
            # the first issue on a new repo undetectable -- see staleness_reason.
            journaled_repos[nwo] = {"path": path, "pushedAt": data.get("pushedAt", ""),
                                    "updatedAt": data.get("updatedAt"),
                                    "activityAt": data.get("activityAt"),
                                    "schemaVersion": data.get("schemaVersion", 1)}
        except Exception:
            journaled_repos[nwo] = {"path": path, "pushedAt": "", "updatedAt": None,
                                    "activityAt": None, "schemaVersion": 0}

    print(f"Found {len(journaled_repos)} existing journal file(s)")

    # 3. Create update-request issues for missing or stale repos
    issues_created = 0
    for nwo, remote in live_repos.items():
        reason = staleness_reason(journaled_repos.get(nwo), remote)

        if reason:
            r = run([
                "gh", "issue", "create",
                "--repo", GITHUB_REPOSITORY,
                "--title", f"update {nwo}",
                "--label", "update-request",
                "--body", f"Automated {reason} journal update for {nwo}",
            ], check=False)
            if r.returncode == 0:
                issues_created += 1
                print(f"  Queued update ({reason}): {nwo}")
            else:
                print(f"  ERROR creating issue for {nwo}: {r.stderr.strip()}", file=sys.stderr)

    print(f"Created {issues_created} update-request issue(s)")

    # 4. Delete journals for repos no longer in the live set
    deleted = []
    for nwo, journal in journaled_repos.items():
        if nwo not in live_repos:
            journal["path"].unlink(missing_ok=True)
            deleted.append(str(journal["path"]))
            print(f"  Deleted stale journal: {nwo}")

    if deleted:
        for path in deleted:
            run(["git", "rm", "--force", "--ignore-unmatch", path])

        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode != 0:
            names = ", ".join(
                p.replace("journals/", "").replace("^", "/").replace(".json", "")
                for p in deleted
            )
            run(["git", "commit", "-m", f"Remove stale journals: {names}"])
            # Retry push with rebase
            for attempt in range(3):
                r = subprocess.run(["git", "push"], capture_output=True, text=True)
                if r.returncode == 0:
                    break
                print(f"  Push failed (attempt {attempt + 1}), rebasing...")
                run(["git", "pull", "--rebase"])
            else:
                print("ERROR: failed to push deletions after 3 attempts", file=sys.stderr)
                sys.exit(1)

        print(f"Deleted {len(deleted)} stale journal(s)")


if __name__ == "__main__":
    main()
