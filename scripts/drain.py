#!/usr/bin/env python3
"""
Drain loop for RepoClerk update-repo workflow.

When triggered by repository_dispatch: processes the payload's owner/repo directly,
then runs the drain loop to pick up any additional pending update-request issues.

When triggered by issues: opened: just runs the drain loop (the newly opened issue
will be found and processed).

Commits journal changes after each drain iteration, then exits once the queue
has been empty for MAX_IDLE_CYCLES * POLL_INTERVAL seconds.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
MAX_IDLE_CYCLES = 3
POLL_INTERVAL = 5  # seconds

# Journal schema version. Bump when the journal shape changes so sync-all re-queues
# existing journals for a one-time backfill. v2 added sourceVolumeChecksum.
SCHEMA_VERSION = 3

GRAPHQL_QUERY = """
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    pushedAt
    repositoryTopics(first: 30) { nodes { topic { name } } }
    issues(states: OPEN, first: 100) {
      nodes {
        number title url
        author { login }
        assignees(first: 20) { nodes { login } }
      }
    }
    pullRequests(states: OPEN, first: 100) {
      nodes {
        number title isDraft url
        author { login }
        closingIssuesReferences(first: 5) {
          nodes {
            number title
            repository { owner { login } }
          }
        }
      }
    }
  }
}
"""


def run(cmd, check=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def fetch_url(url):
    r = subprocess.run(["curl", "-sf", url], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


# A "collection" repo (md-collection topic) is a curated "repo of repos": its README's first
# line is the collection title and its body lists the member repos (typically as GitHub URLs).
# We harvest references liberally here; resolution against the known repo set (and the resulting
# warnings) is deferred to generate-dashboard, which has every journal loaded.
_GITHUB_URL_RE = re.compile(
    r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9_.-]+)", re.I)
# A line that is essentially just `owner/repo` (optionally a markdown list bullet). Kept strict
# so prose like "and/or" is not mistaken for a member reference.
_BARE_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+)\s*$")


def parse_collection_readme(text, self_nwo):
    """Parse a collection repo's README into {title, description, memberRefs}.

    title       = first non-empty line (a leading '#' is stripped).
    description = the first non-empty, non-reference paragraph after the title.
    memberRefs  = de-duped owner/repo strings harvested from GitHub URLs anywhere in the text
                  plus lines that are just `owner/repo`. The collection's own repo is excluded.
    """
    lines = text.splitlines()
    title, title_idx = "", -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s:
            title = s.lstrip("#").strip()
            title_idx = i
            break

    refs, seen = [], set()
    self_key = self_nwo.lower()

    def add(nwo):
        nwo = nwo.strip().rstrip(".,);:")
        if nwo.endswith(".git"):
            nwo = nwo[:-4]
        key = nwo.lower()
        if key and key != self_key and key not in seen:
            seen.add(key)
            refs.append(nwo)

    for m in _GITHUB_URL_RE.finditer(text):
        add(f"{m.group(1)}/{m.group(2)}")
    for line in lines:
        if "github.com" in line:
            continue
        bm = _BARE_LINE_RE.match(line)
        if bm:
            add(bm.group(1))

    description = ""
    for line in lines[title_idx + 1:]:
        s = line.strip()
        if not s:
            if description:
                break
            continue
        if "github.com" in s or _BARE_LINE_RE.match(line) or s.startswith(("#", "-", "*", "+", "<!--")):
            break
        description += (" " if description else "") + s

    return {"title": title, "description": description, "memberRefs": refs}


def resolve_volume_url(volume_ref, name_with_owner):
    """Convert a source_volume reference to a full download URL.
    Mirrors MorphoDepot.resolveVolumeURL: if it starts with 'http' use as-is,
    otherwise treat as a relative path within the repo.
    """
    if volume_ref.startswith("http"):
        return volume_ref
    return f"https://github.com/{name_with_owner}/{volume_ref}"


def process_repo(owner, repo):
    """Query GitHub and write journals/{owner}^{repo}.json. Returns the path written."""
    print(f"  Processing {owner}/{repo}...")

    result = run(["gh", "api", "graphql",
                  "-f", f"query={GRAPHQL_QUERY}",
                  "-f", f"owner={owner}",
                  "-f", f"repo={repo}"])
    data = json.loads(result.stdout)["data"]["repository"]

    topics = [n["topic"]["name"]
              for n in (data.get("repositoryTopics") or {}).get("nodes", [])]

    base_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main"
    accession_raw = fetch_url(f"{base_url}/MorphoDepotAccession.json")
    accession = json.loads(accession_raw) if accession_raw else {}

    captions_raw = fetch_url(f"{base_url}/screenshots/captions.json")
    captions = json.loads(captions_raw) if captions_raw else []

    volume_size = None
    source_volume_raw = fetch_url(f"{base_url}/source_volume")
    if source_volume_raw:
        volume_url = resolve_volume_url(source_volume_raw.strip(), f"{owner}/{repo}")
        r = subprocess.run(
            ["curl", "-sI", "--max-redirs", "10", "-L", volume_url],
            capture_output=True, text=True,
        )
        for line in r.stdout.splitlines():
            if line.lower().startswith("content-length:"):
                # Keep overwriting — redirect responses also emit Content-Length: 0,
                # so we want the last occurrence, which is from the final destination.
                volume_size = int(line.split(":", 1)[1].strip())

    # source_volume_checksum is a committed file whose content is "SHA256:<64-hex>".
    # Journal the bare lowercase hex so duplicate-volume detection can group on it
    # (two repos with the same digest hold byte-identical volumes).
    source_checksum = None
    checksum_raw = fetch_url(f"{base_url}/source_volume_checksum")
    if checksum_raw:
        val = checksum_raw.strip()
        if ":" in val:
            val = val.split(":", 1)[1].strip()
        source_checksum = val.lower() or None

    # CURATOR is a committed plain-text file holding the curating member's GitHub login
    # (one login on the first line). Journal it so the extension can list "repos I curate"
    # from the cache alone — essential for in-org member repos, whose owner is the org
    # (MorphoDepot), not the member, so an owner==me filter would miss them.
    curator = None
    curator_raw = fetch_url(f"{base_url}/CURATOR")
    if curator_raw:
        curator = curator_raw.strip().split("\n")[0].strip() or None

    # A collection repo (md-collection topic) carries no dataset payload; instead its README
    # lists member repos. Parse it now; generate-dashboard resolves the members and renders.
    collection = None
    if "md-collection" in topics:
        readme_raw = fetch_url(f"{base_url}/README.md")
        if readme_raw:
            collection = parse_collection_readme(readme_raw, f"{owner}/{repo}")

    try:
        sc = run(["gh", "api", f"repos/{owner}/{repo}/contents/screenshots",
                  "--jq", '[.[] | select(.name | test("\\.(png|jpg|jpeg|gif|webp)$"; "i"))] | length'])
        screenshot_count = int(sc.stdout.strip() or "0")
    except Exception:
        screenshot_count = 0

    open_issues = [
        {
            "number": i["number"],
            "title": i["title"],
            "url": i["url"],
            "author": i["author"]["login"] if i["author"] else None,
            "assignees": [a["login"] for a in i["assignees"]["nodes"]],
        }
        for i in data["issues"]["nodes"]
    ]

    open_prs = []
    for pr in data["pullRequests"]["nodes"]:
        closing_nodes = pr["closingIssuesReferences"]["nodes"]
        closing_issue = None
        if closing_nodes:
            ci = closing_nodes[0]
            closing_issue = {
                "number": ci["number"],
                "title": ci["title"],
                "repoOwner": ci["repository"]["owner"]["login"],
            }
        open_prs.append({
            "number": pr["number"],
            "title": pr["title"],
            "isDraft": pr["isDraft"],
            "url": pr["url"],
            "author": pr["author"]["login"] if pr["author"] else None,
            "closingIssue": closing_issue,
        })

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    journal = {
        "schemaVersion": SCHEMA_VERSION,
        "nameWithOwner": f"{owner}/{repo}",
        "journalUpdatedAt": now,
        "pushedAt": data["pushedAt"],
        "openIssues": open_issues,
        "openPRs": open_prs,
        "accession": accession,
        "screenshotCount": screenshot_count,
        "screenshotCaptions": captions,
        "volumeSize": volume_size,
        "sourceVolumeChecksum": source_checksum,
        "curator": curator,
    }
    # Only collection repos carry this key, so existing dataset journals are untouched.
    if collection is not None:
        journal["collection"] = collection

    out_path = Path(f"journals/{owner}^{repo}.json")
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(journal, f, indent=2)
        f.write("\n")

    print(f"    Wrote {out_path}")
    return str(out_path)


def commit_and_push(files, message):
    for f in files:
        run(["git", "add", f])
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("  No journal changes to commit.")
        return
    run(["git", "commit", "-m", message])
    # Retry with rebase in case another job pushed concurrently
    for attempt in range(3):
        r = subprocess.run(["git", "push"], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  Pushed ({message})")
            return
        print(f"  Push failed (attempt {attempt + 1}), rebasing...")
        run(["git", "pull", "--rebase"])
    raise RuntimeError(f"Failed to push after 3 attempts")


def main():
    event_name = os.environ.get("EVENT_NAME", "")
    initial_owner = os.environ.get("INITIAL_OWNER", "")
    initial_repo = os.environ.get("INITIAL_REPO", "")

    total_updated = []
    errors = []

    # If triggered by repository_dispatch, process the payload directly first
    if event_name == "repository_dispatch" and initial_owner and initial_repo:
        try:
            path = process_repo(initial_owner, initial_repo)
            commit_and_push([path], f"Update journal: {initial_owner}/{initial_repo}")
            total_updated.append(path)
        except Exception as e:
            print(f"ERROR processing {initial_owner}/{initial_repo}: {e}", file=sys.stderr)
            errors.append(f"{initial_owner}/{initial_repo}")

    # Drain loop: process all pending update-request issues
    idle_cycles = 0
    while idle_cycles < MAX_IDLE_CYCLES:
        result = run(["gh", "issue", "list",
                      "--repo", GITHUB_REPOSITORY,
                      "--state", "open",
                      "--label", "update-request",
                      "--json", "number,title"])
        pending = json.loads(result.stdout)

        if not pending:
            idle_cycles += 1
            if idle_cycles < MAX_IDLE_CYCLES:
                time.sleep(POLL_INTERVAL)
            continue

        idle_cycles = 0
        iteration_files = []

        for issue in pending:
            number = issue["number"]
            title = issue["title"].strip()

            if not title.startswith("update ") or "/" not in title:
                print(f"  Skipping issue #{number}: unrecognized title '{title}'")
                continue

            nwo = title[len("update "):]
            owner, _, repo = nwo.partition("/")
            if not owner or not repo:
                continue

            # Close immediately to dequeue (acts as a mutex)
            subprocess.run(["gh", "issue", "close", str(number),
                             "--repo", GITHUB_REPOSITORY],
                           capture_output=True)

            try:
                path = process_repo(owner, repo)
                iteration_files.append(path)
                total_updated.append(path)
            except Exception as e:
                print(f"  ERROR processing {nwo}: {e}", file=sys.stderr)
                errors.append(nwo)

        if iteration_files:
            repos_str = ", ".join(
                p.replace("journals/", "").replace("^", "/").replace(".json", "")
                for p in iteration_files
            )
            try:
                commit_and_push(iteration_files, f"Update journals: {repos_str}")
            except Exception as e:
                print(f"  ERROR committing: {e}", file=sys.stderr)
                time.sleep(POLL_INTERVAL)

    unique = len(set(total_updated))
    print(f"\nDrain loop complete. Updated {unique} journal(s), {len(errors)} error(s).")
    if errors:
        sys.exit(1)


main()
