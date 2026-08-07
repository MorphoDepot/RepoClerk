# RepoClerk journal freshness

**Status: partially implemented. The push path works, is correctly configured, and structurally
covers only the org tier.** Personal-account repos — the tier used for classrooms — have no push
path at all, confirmed rather than inferred. Every fallback beneath it was found broken on
2026-08-07; the sweep (Layer 1) is now fixed and runs every 15 minutes, so staleness is bounded for
the first time. Open for review; see *Decisions needed* at the end.

**Latency target:** ≤ 2–3 minutes end-to-end (an instructor's action → visible in attendees'
extension). Sub-second is *not* required.

> **History.** This file previously described the webhook design as *"implemented, deployed, and
> verified end-to-end"* full stop. That was true of what it was tested against — an org repo — and
> the verification was real. It was not true of the fleet. Rewritten 2026-08-07 after a live class
> failed in a way the document said was solved.

---

## The concern: live workshops are the one real-time case

MorphoDepot usage is overwhelmingly **asynchronous** — a PI publishes a repo, segmentors work over
days or weeks. There, eventual consistency is invisible and completely fine.

The one scenario where freshness matters is a **live workshop / classroom**: an instructor creates a
repository on the spot and asks attendees to create issues and start segmenting *immediately*. In
that burst, attendees need the new repo (Search), their new issues (Annotate), and their PRs
(Review) to appear within a couple of minutes — otherwise people sit staring at empty lists during
the session.

The extension's list views read pre-computed journals **by design**, to avoid hammering GitHub with
topic-wide searches as the repo count grows — the whole reason RepoClerk exists. So freshness is
entirely a property of how fast a change reaches the journal, and every failure below is a failure
of a *trigger*, never of the read path.

---

## What was built, and what it actually covers

An **org webhook** on `MorphoDepot` for `repository`, `issues`, `pull_request`, `release`, `push`
delivers to `POST /github/webhook` on the intake app (`morphodepot-intake#18`), which verifies the
HMAC, filters to the `morphodepot` topic, coalesces per-repo over ~8 s, and fires a
`repository_dispatch` at `MorphoDepot/RepoClerk`, where `update-repo.yml` already accepts it.

**This works.** Verified 2026-06-19 at ~20 s from issue to fresh journal, and still live: 31 of the
last 60 drain runs were dispatch-driven. No extension change was needed and none is proposed here.

**It covers repos in the `MorphoDepot` org, and nothing else.** An org webhook fires for that org's
repositories. MorphoDepot's two-tier model puts *archival* repos in the org and *short-term* repos in
the curator's personal account — and short-term is the classroom tier. The document's motivating
scenario is therefore the scenario the implementation does not reach.

### Evidence

`muratmaga/rana-clamitans-full-body` — a personal repo whose own journal records
`repoType: "Short-term (e.g. repositories for classroom exercises...)"` — ran a class on 2026-08-07
from 18:22 to 19:00 UTC. In that window:

| | |
|---|---|
| Dispatch-driven drain runs | **0** |
| Most recent dispatch before it | 17:55:59Z |
| Org repos that refreshed unaided the same afternoon | `mus-musculus-E15` 17:42, `neurotrichus-gibbsii-skull` 17:56 — both matching dispatch timestamps |

Five issues were created 18:28:48–18:30:05 and five annotators saw an empty Annotate tab. Two PRs
opened at 18:43 and 18:50 never reached the curator's Review tab. Every journal in that window was
corrected by hand.

> **Confirmed 2026-08-07, not inferred.** This started as a deduction from delivery timing; both
> halves have since been checked directly.
>
> An org owner confirmed the org hook is correctly configured and subscribes to all five events
> (`repository`, `issues`, `pull_request`, `release`, `push`). Nothing is misconfigured — the hook
> does exactly what it was built to do, for the repositories an org hook can see.
>
> And there is no second path. The `morphodepot-intake` GitHub App (`app_id 3948287`), which is the
> other mechanism that could deliver events for a personal repo, **subscribes to no webhook events
> at all**:
>
> ```
> slug=morphodepot-intake  events=  perms=contents:write,issues:write,members:write,metadata:read
> ```
>
> An empty `events` list means it delivers nothing, ever. So the org hook is the only push path into
> RepoClerk, and an org hook fires only for that org's repositories.
>
> The gap is therefore structural rather than a defect: nothing is broken, the mechanism simply does
> not span the tier where classrooms happen.

---

## Every fallback beneath the webhook is also broken

The webhook was designed with two backstops. Both were found non-functional on 2026-08-07, which is
why the gap produced a total outage on the uncovered tier rather than a delay.

### 1. `notifyRepoClerk()` is a silent no-op for non-org users — [#469](https://github.com/MorphoDepot/RepoClerk/issues/469)

The extension enqueues by opening an issue here with `--label update-request`. **GitHub silently
drops the `labels` field when the author lacks triage permission on the target repo.** Issue created,
HTTP 201, `gh` exits 0, no label. Both consumers gate on that label (`update-repo.yml:19-23`,
`drain.py:317-321`), so the request is created, never processed, never closed.

Every annotator is a non-collaborator. This path has only ever worked for org members — precisely the
population that tested it. Unlabeled orphans are still open going back to 2026-06-20, so it has been
broken for roughly seven weeks. Requests from the project's own testing accounts (`amm554`,
`SlicerMorph`) are among them: the bug was reproducing *during testing* and emitted no signal.

The extension cannot detect this either. `hasRepoClerkUpdatePending()` filters on the same label, so
it reports "nothing pending", `_waitForRepoClerkUpdate()` short-circuits, and Refresh silently shows
stale data. The detector is blind in exactly the case it exists to detect.

### 2. The `sync-all` cron keys staleness on the wrong field — [#470](https://github.com/MorphoDepot/RepoClerk/issues/470)

`sync-all.py:94` compares `journal["pushedAt"] != remote_pushed_at`. `pushedAt` is the last **git
push** time. Issue events do not move it, and a fork's PR does not move the upstream repo's. So the
hourly backstop cannot catch either class of change.

An audit of all 80 journals that afternoon found 10 assigned issues across 6 repos unreachable from
the extension. Of the 43 journals carrying any open issues, nearly all were stamped 2026-06-15 — the
schema-v3 backfill day. Issue data had been frozen since then except where an unrelated push
happened to shake a repo loose.

### 3. The writer collides with itself — about a third of dispatches fail

Of the last 31 dispatch-driven runs, **10 failed**, all identically:

```
ERROR processing MorphoDepot/mus-musculus-E15:
  Command '['git', 'pull', '--rebase']' returned non-zero exit status 1
Push failed (attempt 1), rebasing...
Drain loop complete. Updated 0 journal(s), 1 error(s).
```

Concurrent writers racing on the journals branch. This document previously claimed push + coalesce
"eliminates the concurrency-skip fragility"; it changed its shape rather than removing it. A failed
dispatch is indistinguishable from no event at all, so the repo stays stale until the cron — which,
per (2), cannot see the change either.

### The compound result

For a repo in the personal tier there is **no path at all** by which a new issue or a fork PR reaches
the journal: no webhook, a notify that silently no-ops, and a cron that keys on a field the event
does not move. It is not a delay. It never arrives.

---

## Why none of this was visible

Worth stating separately, because it is the property most likely to recur.

Every failure mode here **succeeds loudly and fails silently**. `gh issue create` returns 201 with a
URL whether or not the label stuck. A cancelled or failed Action leaves no user-facing trace. A stale
journal renders as an empty list, which is indistinguishable from "no work assigned to you". There is
no freshness metric anywhere, so nothing could have been noticed short of someone comparing a journal
against GitHub by hand — which is how it was eventually found.

Compounding it: the affected population (non-members, personal-tier repos) is disjoint from the
testing population (org owners, org repos). The system was correct on everything anyone looked at.

---

## Proposal

Four layers, ordered so that each is useful alone and none depends on the next.

### Layer 1 — Make the pull path authoritative (the correctness floor)

Fix [#470](https://github.com/MorphoDepot/RepoClerk/issues/470): give the sweep a change token that
actually moves when issues and pull requests move.

**Not `Repository.updatedAt`.** An earlier draft of this section proposed exactly that, and it is
wrong — `updatedAt` tracks the repository *record* (description, topics, visibility), not its issues.
Measured against live repos on 2026-08-07, before writing any code:

| repo | `updatedAt` | newest issue |
|---|---|---|
| `jaimigray/snakeseg` | 2025-10-07T16:46:18Z | 2026-07-07T11:39:01Z |
| `dinonoto/Juvenile_Bearded_Dragon` | 2026-06-09T19:01:40Z | 2026-06-11T12:55:30Z |
| `muratmaga/rana-clamitans-full-body` | 2026-08-07T18:28:07Z | 2026-08-07T18:30:05Z |

`snakeseg` had nine months of issue activity that never touched `updatedAt`. Comparing it would have
shipped a fix that changed nothing, and the tests would have passed, because the assumption was in
the fixture as much as the code.

The token that does work is an **activity watermark**: the newest issue-or-PR update time, from
`issues(first: 1, orderBy: {field: UPDATED_AT, direction: DESC})` and the same for
`pullRequests`. Deliberately unfiltered by state — closing an issue must move the watermark forward,
whereas a `states: OPEN` filter would make it jump *backwards* to the next-newest open item. It is
strictly better than an open-issue `totalCount`, which cannot see a reassignment or a
close-one-open-one within the same window.

Cost, measured on the whole 80-repo fleet: **2 GraphQL points** against a 5,000/hour budget, in the
discovery search that already runs. The `first: 1` is what keeps it that cheap — the same search
pulling 100 issues and 100 PRs per repo costs 202.

Three tokens are journaled, none redundant:

| token | moves on |
|---|---|
| `pushedAt` | git pushes — gates the expensive artifact re-fetch (accession, captions, volume HEAD) |
| `updatedAt` | the repository record — a collection's title is its description, so this still matters |
| `activityAt` | newest issue or PR update — the one that fixes the reported bug |

| file | change |
|---|---|
| `sync-all.py` | search query and JQ filter gain the three tokens; `staleness_reason()` extracted as a pure function and extended; the journal read |
| `drain.py` | `GRAPHQL_QUERY` gains `updatedAt` and the two aliased `first: 1` connections; new `activity_watermark()`; `process_repo()` writes both fields |
| `constants.py` *(new)* | `SCHEMA_VERSION`, imported by both — it was `2` in `sync-all.py` and `3` in `drain.py`, which silently disabled the schema-upgrade backfill entirely |
| `test_staleness.py` *(new)* | covers each token, the pre-upgrade path, and a regression test that fails if anyone swaps the watermark back for `updatedAt` |

Both scripts also gained `if __name__ == "__main__"` guards, without which the logic cannot be
imported by a test at all.

Adding journal fields is a schema change, so `SCHEMA_VERSION` goes to 4 and every journal is
re-queued once. That is desirable — it clears the accumulated staleness in the same pass, and it is
what stops a journal with no `activityAt` from being reported stale on every sweep forever.

**Why this first:** it is the only layer that makes correctness independent of who the actor is, what
permissions they hold, and which tier the repo lives in. It also happens to be the only fix for the
original classroom failure, where an instructor assigned issues in the web UI — an event the
extension never sees and no notify path can ever cover.

Once this holds, everything else is latency, and a latency mechanism is allowed to fail.

### Layer 2 — Close the tier gap

An org webhook cannot cover personal repos. Options, best first:

1. **GitHub App installation events.** The App is already installed on curators' personal accounts,
   so if it subscribed to `issues` / `pull_request` / `push` / `repository`, GitHub would deliver
   events for every repo it can see — personal and org alike, to the same receiver. One uniform
   path, no per-repo setup, nothing for a curator to do.

   Its current state is known exactly (`gh api orgs/MorphoDepot/installations`):

   ```
   app_id=3948287  slug=morphodepot-intake
   events=
   perms=contents:write,issues:write,members:write,metadata:read
   ```

   (`members: write` in that list is the onboarding team-grant, not a leftover from the
   org-administration removal — worth confirming before anyone prunes it on sight.)

   So the work is: subscribe to the four events (there are none today), and add
   `pull_requests: read` (absent entirely — `issues: write` is already held, so issues need no
   permission change). That is a real permission *increase* on an App that was deliberately narrowed
   when org-administration was removed, and it should be argued on its merits rather than slipped in
   — but it is a smaller increase than it first appeared.
2. **Per-repo webhook created at publish time** by the extension using the curator's own token, which
   already has admin on their own repo. No App permission change, but it is per-repo setup that can
   fail silently on exactly the repos that need it, and it leaves nothing covering repos created
   before the change.
3. **Do nothing here and rely on Layer 1**, accepting sweep-interval freshness for the personal tier.
   The interval is now 15 minutes (see *Sweep interval* below), which is well short of the 2–3 minute
   target but a long way from the unbounded staleness this started as.

### Layer 3 — Make failure loud

Non-negotiable regardless of which of the above lands, because the absence of this is why seven weeks
passed.

- `notifyRepoClerk()` re-reads the issue it created and warns when the label did not stick.
- `hasRepoClerkUpdatePending()` stops filtering on the label, so the extension can **see** its own
  dropped request and say so. Scope this to *warning the user*, not to making wait-and-retry succeed:
  the drain at `drain.py:317-321` is still label-gated, so an unlabeled request will never be
  processed no matter what the client believes. Detecting a request the drain will never honor and
  then waiting on it is strictly worse than today's "nothing pending" — it replaces a wrong answer
  with a hang.

  Wait-and-retry only becomes honest once the drain itself matches on title
  ([#469](https://github.com/MorphoDepot/RepoClerk/issues/469)), and that change carries its own
  blocker: dropping the label from the drain's query returns every open issue in this repo, including
  ordinary discussion, which the title check skips *without closing*. `pending` is then never empty,
  `idle_cycles` never increments, and since `time.sleep()` is only reached in the `not pending`
  branch it becomes a tight API loop until the 30-minute timeout. The fix must compute `pending` from
  the title-matched set before the emptiness test.

  Note also that if Refresh is ever made to trigger a drain, that change must ship together with a
  working detection path — a drain that runs while the client cannot tell is worse than no drain at
  all.
- An open, unlabeled `update ...` issue older than a few minutes is a known-bad state and should
  alarm.
- Dispatch and drain *failures* should alarm. Ten silent failures in 31 runs is the current baseline.
- **Publish a freshness metric**: max and median `now - journalUpdatedAt` against `activityAt`,
  on the dashboard. One number that would have made all of this obvious on day one.

### Layer 4 — Make it affordable at 5–10× the current size

Today's fleet is 80 repos. The design should hold at 500.

Measured on 2026-08-07: a steady-state `sync-all` run is 25–28 s, almost entirely checkout and setup
— the discovery search is a single page. `journals/` is 352 KB across 80 files (~4.4 KB each),
`docs/` is 264 KB, `.git` is 3.9 MB.

**Discovery scales fine.** 500 repos is 5 search pages. This is not the constraint at any plausible
size.

**The drain does not, for two reasons.**

*Per-repo cost is indiscriminate.* `process_repo()` makes ~7 round-trips including a `curl -sI -L`
HEAD against the volume URL — an S3 redirect chain on a multi-GB object — and runs all of them on
every refresh, including one triggered by a single issue comment. Split the two signals by cost:
**`pushedAt` gates the expensive artifact fetches** (accession, captions, `CURATOR`, checksum, volume
HEAD — none of which can change without a push), **`activityAt` gates a cheap issues/PRs-only
refresh**. Then batch that cheap refresh with GraphQL aliases (`r0: repository(...) r1: ...`), so 500
repos cost ~10 requests rather than 500. Verify node-count and complexity limits before fixing a
batch size.

*There is one writer and it already collides.* The `repoclerk-writer` concurrency group keeps one
run in flight and one pending and cancels the rest; two long runs were cancelled outright on
2026-08-06. Combined with the rebase failures above, the writer is the first hard ceiling. Serializing
properly — a queue rather than a lock that drops work — matters more as the fleet grows.

**Git churn is the wall, and it is client-visible.** Every drain regenerates `docs/` and commits it.
At 500 repos with a tightened sweep interval that is hundreds of commits a day, each rewriting a
growing generated tree, on a repository **every extension user clones**. `refreshRepoClerk()` does a
`--depth 1` clone then pulls forever after, and there is already a `REPOCLERK_SIZE_LIMIT_MB = 100`
guard that deletes and re-clones when the working copy crosses 100 MB — a guard whose existence says
someone already anticipated this. Two changes, in order of effort:

1. **Get `docs/` off the branch clients clone** (`gh-pages` or an orphan branch). Small change, large
   effect: presentation churn stops landing in every user's working copy.
2. **Reconsider git as the client transport.** At 500 repos the whole journal set is ~2.2 MB. One
   aggregated JSON fetched with an `ETag` is a single conditional GET — no history, no size guard, no
   periodic re-clone. Git is supplying history that nothing reads, for data that is a cache.

---

### Sweep interval

**Now 15 minutes** (`8,23,38,53 * * * *` — keeping the original off-peak offset rather than `*/15`,
which would land every fourth run on the congested top of the hour). Previously hourly.

Because Layer 1 made the sweep authoritative, this interval *is* the staleness ceiling for anything
the push path does not cover — which today means every personal-tier repo, and every web-UI action
on any repo. Measured costs per sweep:

| | |
|---|---|
| API | 4 GraphQL points (two topic searches at 2 each) against 5,000/hour — about 1% at 15-minute cadence |
| Runner time | 25–28 s for a quiet sweep; free, this is a public repo |
| Commits | **none** when nothing changed — the dashboard push short-circuits on an empty diff, so idle sweeps do not grow the repository every client clones |

**Why not 5 minutes**, which is GitHub's floor and was the original ambition:

1. **A busy sweep outlasts a 5-minute period.** The 80-repo v4 backfill took 5m53s. Since `sync-all`
   and `update-repo` share `concurrency: repoclerk-writer` with `cancel-in-progress: false`, GitHub
   keeps one running plus one pending and *discards* the rest — so the schedule would silently
   degrade precisely under the load that motivated tightening it.
2. **`sync-all` does not dedup against open requests.** It creates an `update-request` for every
   stale repo on every run, with no check for one already open. At hourly the drain always clears
   them in between; at 5 minutes a single stuck drain collects a duplicate issue per repo per period.

Both are prerequisites for going faster, and both are Layer 4 work (writer contention, and a dedup
check in `sync-all.main()`). Worth noting that GitHub treats scheduled runs on public repos as
best-effort and drops them under load — the hourly schedule was already firing 16 times in 24 hours,
not 24 — so any interval here is a target rather than a guarantee.

## Open question: what belongs in the journal at all

Stated as an open decision rather than a recommendation, because it cuts against something this
document already rejected.

The journal currently caches two very different things. **Slow-moving facts** — which repos exist,
accession metadata, screenshots, volume size — are an excellent fit: expensive to compute, rarely
changing, genuinely worth precomputing. **Fast-moving state** — open issues, open PRs, draft status,
assignees — is a poor fit: it changes constantly, is cheap to fetch for a handful of repos, and is
the *only* part that has ever been observed stale. Every bug in this document is a bug about the
second category.

Splitting them would mean: journal for discovery, live REST for the issues and PRs of the specific
repos a given user cares about. That eliminates the entire failure class rather than instrumenting
it, and it collapses the churn and scaling problems in Layer 4 at the same time, since a journal that
only changes on push barely changes at all.

**The counter-argument is recorded in this file's own history**, under *Rejected alternative:
client-side source switching*: it scatters source-selection logic through the client, teaches the
client to distrust the cache, and needs a per-repo "active repo" notion. That objection was aimed at
`if active_repo: query live; else: read cache` — per-repo special-casing — and a uniform split by
data category is not the same shape. But it is close enough that the decision should be made
deliberately, in the open, by people who did not write either proposal.

Note also that the test harness already reaches this conclusion in one place: it declines to use
`logic.issueList()` and queries GitHub directly, because the cached path was not reliable enough to
test against.

---

## Decisions needed

1. ~~Confirm the tier gap against the actual org-hook configuration.~~ **Done 2026-08-07.** The hook
   subscribes to all five events and is correctly configured; the App subscribes to none. The gap is
   structural, not a misconfiguration — see *Evidence*.
2. Layer 2: App-installation events (a permission increase on an App deliberately narrowed to
   Contents-only), per-repo webhooks, or neither?
3. Layer 4: is 500 repos the number to design for? The answer differs sharply between 500 dormant
   archival repos and 500 with several classes live at once.
4. The open question above — is the cache boundary redrawn, or is the journal instrumented and kept
   as it is?
5. ~~Sweep interval once Layer 1 lands.~~ **Decided: 15 minutes** — see *Sweep interval* below.
   Going below that needs two prerequisites, both listed there.

## Related

- [#469](https://github.com/MorphoDepot/RepoClerk/issues/469) — label gate / silent notify failure
- [#470](https://github.com/MorphoDepot/RepoClerk/issues/470) — `pushedAt` staleness, `SCHEMA_VERSION` drift
- [SlicerMorph/SlicerMorphoDepot#211](https://github.com/SlicerMorph/SlicerMorphoDepot/issues/211) — investigation hub, extension-side work
- [SlicerMorph/SlicerMorphoDepot#212](https://github.com/SlicerMorph/SlicerMorphoDepot/issues/212) — Review tab hides drafts silently; not a freshness bug, but it made this one much harder to diagnose
- `morphodepot-intake#18` — the webhook receiver
