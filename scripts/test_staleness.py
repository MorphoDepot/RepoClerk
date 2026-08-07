#!/usr/bin/env python3
"""Unit tests for sync-all.py's staleness_reason() and drain.py's activity_watermark().

Run: python3 scripts/test_staleness.py   (no deps, no network)

The bug these guard against (#470): the staleness test used to compare only pushedAt,
which moves on a git push and nothing else.  Opening, assigning, or closing an issue does
not move it, and a fork's pull request does not move the *upstream* repo's.  So once a
journal was written with a current pushedAt, every issue and PR event afterwards was
invisible to the hourly sweep -- permanently, not for an hour.

The trap these also guard against: Repository.updatedAt looks like the obvious fix and is
not.  It tracks the repository *record*, not its issues.  Measured on live repos
2026-08-07:

    repo                              updatedAt             newest issue
    jaimigray/snakeseg                2025-10-07T16:46:18Z  2026-07-07T11:39:01Z
    dinonoto/Juvenile_Bearded_Dragon  2026-06-09T19:01:40Z  2026-06-11T12:55:30Z
    muratmaga/rana-clamitans...       2026-08-07T18:28:07Z  2026-08-07T18:30:05Z

snakeseg's updatedAt was nine months behind its issue activity.  Comparing it would have
shipped a fix that changed nothing.  See design/near-realtime-ingestion.md.
"""
import importlib.util
from pathlib import Path


def _load(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sync_all = _load("sync-all.py", "sync_all")
drain = _load("drain.py", "drain")

CURRENT = sync_all.SCHEMA_VERSION
PUSH = "2026-08-07T18:22:43Z"
META = "2026-08-07T18:28:07Z"
SEEN = "2026-08-07T18:28:30Z"
LATER = "2026-08-07T18:30:05Z"


def journal(pushed=PUSH, updated=META, activity=SEEN, schema=CURRENT):
    return {"pushedAt": pushed, "updatedAt": updated, "activityAt": activity,
            "schemaVersion": schema}


def remote(pushed=PUSH, updated=META, activity=SEEN):
    return {"pushedAt": pushed, "updatedAt": updated, "activityAt": activity}


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name


def test_current_journal_is_left_alone():
    check("a journal matching on all three tokens is not re-queued",
          sync_all.staleness_reason(journal(), remote()) is None)


def test_missing_journal():
    check("no journal file at all -> missing",
          sync_all.staleness_reason(None, remote()) == "missing")


def test_push_still_detected():
    check("a new push -> stale",
          sync_all.staleness_reason(journal(), remote(pushed=LATER)) == "stale")


def test_issue_activity_without_a_push():
    """The regression that caused the 2026-08-07 classroom outage.

    Five issues were created on a repo 18 seconds after its journal was written.  No push
    accompanied them, so pushedAt was unchanged and the sweep saw nothing to do.
    """
    check("issue activity with no push -> activity (was: ignored forever)",
          sync_all.staleness_reason(journal(), remote(activity=LATER)) == "activity")


def test_fork_pr_does_not_need_a_push():
    # A fork's PR leaves the upstream pushedAt untouched but does move the newest-PR
    # watermark, so it reaches the journal by the same route as an issue.
    check("a fork's pull request -> activity",
          sync_all.staleness_reason(journal(), remote(activity=LATER)) == "activity")


def test_repo_updated_at_alone_would_have_missed_it():
    """Documents why activityAt exists and updatedAt is not a substitute.

    This is the live snakeseg shape: the repository record has not been touched in months
    while issues kept moving.  If staleness keyed on updatedAt, this returns None and the
    journal never refreshes.
    """
    stale_meta = journal(updated="2025-10-07T16:46:18Z", activity="2026-06-01T00:00:00Z")
    live = remote(updated="2025-10-07T16:46:18Z", activity="2026-07-07T11:39:01Z")
    check("issue activity while updatedAt sits still -> still caught",
          sync_all.staleness_reason(stale_meta, live) == "activity")


def test_repo_metadata_change():
    # A collection's title comes from the repo description, so a record change must
    # re-drain even with no push and no issue activity.
    check("description or topic change -> metadata",
          sync_all.staleness_reason(journal(), remote(updated=LATER)) == "metadata")


def test_pre_upgrade_journal_upgrades_once_not_forever():
    """A journal written before activityAt existed must not be reported stale forever.

    Comparing "" against a live timestamp would match on every sweep, re-queueing the
    whole fleet hourly for good.  Empty values fall through to the schema check, which
    re-drains each repo exactly once and writes the field.
    """
    old = journal(updated="", activity="", schema=CURRENT - 1)
    check("pre-upgrade journal -> schema-upgrade, not activity",
          sync_all.staleness_reason(old, remote(activity=LATER)) == "schema-upgrade")
    check("after the upgrade drain it is quiet again",
          sync_all.staleness_reason(journal(activity=LATER), remote(activity=LATER)) is None)


def test_pushed_at_takes_precedence():
    # Everything moved: report the strongest signal, since a push means the expensive
    # artifacts (accession, captions, volume size) genuinely need re-fetching.
    check("all tokens moved -> stale",
          sync_all.staleness_reason(journal(), remote(pushed=LATER, updated=LATER,
                                                      activity=LATER)) == "stale")


def test_unreadable_journal_is_re_queued():
    # main() represents an unparseable journal file as empty strings + schemaVersion 0.
    check("corrupt journal record -> re-queued rather than skipped",
          sync_all.staleness_reason(
              {"pushedAt": "", "updatedAt": "", "activityAt": "", "schemaVersion": 0},
              remote()) == "stale")


def test_watermark_matches_between_the_two_scripts():
    """drain writes the watermark, sync-all reads it -- they must compute it identically."""
    data = {"latestIssue": {"nodes": [{"updatedAt": SEEN}]},
            "latestPR": {"nodes": [{"updatedAt": LATER}]}}
    check("watermark is the newer of the two", drain.activity_watermark(data) == LATER)

    check("PR newer than issue is handled either way round",
          drain.activity_watermark({"latestIssue": {"nodes": [{"updatedAt": LATER}]},
                                    "latestPR": {"nodes": [{"updatedAt": SEEN}]}}) == LATER)

    check("a repo with no issues and no PRs -> empty, never None",
          drain.activity_watermark({"latestIssue": {"nodes": []},
                                    "latestPR": {"nodes": []}}) == "")

    check("missing keys entirely -> empty",
          drain.activity_watermark({}) == "")

    # sync-all builds the same value from its own query shape; a divergence here would
    # make every repo look permanently stale.
    check("a fresh repo with neither is quiet, not permanently stale",
          sync_all.staleness_reason(journal(activity=""), remote(activity="")) is None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print("\nall staleness tests passed")
