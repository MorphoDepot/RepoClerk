"""Constants shared by drain.py and sync-all.py.

This module exists so the two scripts cannot drift apart again: SCHEMA_VERSION was
2 in sync-all.py while drain.py emitted 3, which silently disabled the whole
schema-upgrade backfill path (sync-all would never flag a v3 journal as needing
a rewrite, because it believed 2 was current).

It is a separate module rather than the constant living in one script and being
imported by the other: a script is an entry point, not a library, and importing
one to reach a constant couples the two in the wrong direction.  (Until this
change both scripts also ended in a bare `main()` call, so importing either would
have run it as a side effect.  Both now carry `if __name__ == "__main__"` guards,
which is what makes staleness_reason() testable.)

Both scripts are invoked as `python3 scripts/<name>.py` from the repo root, which
puts `scripts/` on sys.path[0], so a plain `import constants` resolves.
"""

# Journal schema version. Bump when the journal shape changes so sync-all re-queues
# every existing journal for a one-time backfill.
#   v2 added sourceVolumeChecksum
#   v3 added curator / collection
#   v4 added updatedAt (repository-record change token) and activityAt (newest issue-or-PR
#      update time -- the signal Repository.updatedAt looks like it provides and does not;
#      see design/near-realtime-ingestion.md and #470)
SCHEMA_VERSION = 4
