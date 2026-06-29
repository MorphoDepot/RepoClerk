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

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

# Keep in sync with drain.SCHEMA_VERSION. A journal whose schemaVersion is below this
# is treated as stale, so a schema bump backfills every journal on the next sync.
SCHEMA_VERSION = 2


def run(cmd, check=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def search_topic(topic):
    """Return {nameWithOwner: pushedAt} for live repos carrying `topic` (forks included)."""
    result = run([
        "gh", "api", "graphql",
        "--paginate",
        "--jq", ".data.search.nodes[] | {nameWithOwner, pushedAt}",
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
                ... on Repository {{ nameWithOwner pushedAt }}
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
            repos[entry["nameWithOwner"]] = entry.get("pushedAt", "")
    return repos


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
            journaled_repos[nwo] = {"path": path, "pushedAt": data.get("pushedAt", ""),
                                    "schemaVersion": data.get("schemaVersion", 1)}
        except Exception:
            journaled_repos[nwo] = {"path": path, "pushedAt": "", "schemaVersion": 0}

    print(f"Found {len(journaled_repos)} existing journal file(s)")

    # 3. Create update-request issues for missing or stale repos
    issues_created = 0
    for nwo, remote_pushed_at in live_repos.items():
        journal = journaled_repos.get(nwo)
        if journal is None:
            reason = "missing"
        elif journal["pushedAt"] != remote_pushed_at:
            reason = "stale"
        elif journal.get("schemaVersion", 1) < SCHEMA_VERSION:
            reason = "schema-upgrade"
        else:
            reason = None

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


main()
