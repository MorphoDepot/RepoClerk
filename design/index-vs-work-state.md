# Splitting the journal: discovery index vs work state

**Status: proposal, for discussion. Nothing here is implemented.**
Raised by @muratmaga on 2026-08-07, after a day spent fixing the freshness bugs recorded in
`near-realtime-ingestion.md`. It supersedes that file's *Open question: what belongs in the journal
at all*.

## Design target

**Thousands of personal repos and hundreds of org repos.** Everything below is written against that
number, not against the fleet as it stands.

Today's fleet is 80 repos and the org tier is a week old. Every measurement in this document was
taken at that size, which is **one to two orders of magnitude** below where this infrastructure needs
to work. So the counts are useful only for establishing *how a cost scales* — the exponent, not the
value. An argument of the form "there is not much of X today" is not admissible; X will be a hundred
times larger, and a design that only works at the current size is not a design.

The measurements are reported as `per unit × target` for that reason.

## The proposal

Take **issues and pull requests out of the journal for personal repos** and fetch them live from
GitHub, per user, when needed. Keep everything else — species, modality, spacing, dimensions,
screenshots, volume size and checksum, curator — in a journal that **all** repos appear in, personal
and org alike, refreshed periodically.

Personal repos stay **discoverable** through Search. They stop being **advertised** on the dashboard,
which becomes the visible benefit of publishing into the org, alongside collections, releases, and
DOIs.

Requirement set alongside it: **a repo must be findable within one hour of publishing.**

### Optional extension: do the same for org repos

Treating org work state the same way — live, not journaled — is a separate decision and is *not* part
of the proposal above. Arguments both ways, with no appeal to how much data either tier holds today:

**For.** The per-user query is not tier-scoped. `GET /issues?filter=assigned` returns every issue
assigned to you across all of GitHub in a single call, org and personal together. Keeping org work
state journaled means taking that response, discarding the org rows, and re-reading those same issues
from the journal — more code, two paths, and a staler answer, since a live query is fresher than any
cache. The org tier gains nothing from being cached here; a direct query beats the ~20-second webhook
path plus the client's next refresh.

**Against.** It is a larger change, and it commits both tiers at once rather than letting the live
path prove itself on one first. It also supersedes work already shipped: under the narrow proposal
the sweep still needs `activityAt` (#475) to detect org issue activity, so that stays load-bearing;
under the extension it becomes a field with no consumer.

The narrow version is the reversible one. Take personal out, run a class on it, then decide.

## Why: the scaling properties

Four arguments, none of which depend on how many repos or issues exist right now.

### 1. The two data classes have different scaling exponents

| | cost scales with | at target |
|---|---|---|
| **Discovery index** | fleet size | thousands of repos — must be computed once, centrally, and shared |
| **Work state** | repos *one person* works on | a handful — a property of how people work, not of fleet size |

This is the whole argument, and it is why RepoClerk exists for one and not the other. Measured at 80
repos: a fleet-wide crawl of issues and PRs costs **202 GraphQL points** on the 30-per-minute search
endpoint, and grows linearly — roughly 2,500 points at 1,000 repos, which exhausts a 5,000/hour
budget in two refreshes. The same data fetched per user costs **1–14 points** and *does not move when
the fleet grows*, because it is bounded by personal involvement.

Journaling work state puts fleet-scaling cost behind data that only ever needs per-user scope.

### 2. Full-fleet operations do not survive an order of magnitude

The schema-v4 backfill on 2026-08-07 gives clean per-repo costs for a whole-fleet pass:

| phase | 80 repos | per repo | at 1,000 | at 5,000 |
|---|---|---|---|---|
| discover + queue | 81 s | 1.01 s | 17 min | 84 min |
| drain | 259 s | 3.24 s | 54 min | 4.5 h |
| **total** | **5 m 53 s** | | **~1.2 h** | **~6 h** |

Against `timeout-minutes: 30`, a single writer, and a `concurrency` group that discards overlapping
runs. At target scale a full-fleet pass is not one job; it is a dozen sequential ones, and the queue
phase alone exceeds the timeout.

Worse, the queue is built from GitHub issues — one `gh issue create` per repo. A full-fleet operation
would open thousands of issues, against a `GITHUB_TOKEN` limit documented in the low thousands of
REST requests per hour. **The issue-as-queue mechanism has a ceiling around a thousand items an
hour**, which today's fleet never approaches and the target fleet exceeds on a single migration.
(Verify the exact figure and how it is scoped against current GitHub documentation before relying on
it — rate limits are per token, and the numbers move. The order of magnitude is the point.)

The design consequence is general: **the fewer full-fleet operations the architecture requires, the
better it scales.** Journaling work state guarantees frequent per-repo drains driven by fleet-wide
activity. An index-only journal drains only on push.

### 3. Journal churn is driven by whatever is journaled

Every drain commits, regenerates the dashboard, and pushes — to a repository **every client clones
and pulls**.

- **Index-only churn** is bounded by the push rate. Content changes require a git push; measured at 80
  repos, 57 had not been pushed in over 90 days, and everything Search reads (`widget_search.updateSearchResults`
  — species, modality, spacing, dimensions, size, captions) comes from files committed inside the
  repo and cannot change without one. Publishing is a once-per-repo event, so this scales with *new
  repos*, not with the fleet.
- **Work-state churn** is bounded by fleet-wide issue and pull-request activity, which scales with
  fleet size *multiplied by* per-repo activity. It is the faster-growing term by a wide margin.

At target scale the second term is what makes the dashboard rebuild — currently 3 s over 80 repos,
itself O(fleet) — run on every issue comment anywhere in the fleet.

### 4. Git as the client transport has a ceiling

Journals average ~4.4 KB. At thousands of repos the set is tens of megabytes, cloned and then pulled
forever by every extension user, with history accumulating on top. `refreshRepoClerk()` already
carries a `REPOCLERK_SIZE_LIMIT_MB = 100` guard that deletes and re-clones when the working copy
crosses 100 MB — a guard whose existence says someone already saw this coming.

This is a problem for the index regardless of this proposal, and it is worth solving separately:
publish `docs/` off the branch clients clone, and consider a compact searchable index (the handful of
fields Search actually filters on, a few hundred bytes per repo) with full detail fetched lazily,
served as a conditional GET rather than a git history. But note the direction of the interaction —
**removing work state from the journal reduces both its size and its churn**, so it makes this ceiling
further away rather than nearer.

## Measured costs

Taken 2026-08-07 at 80 repos. Reported to establish scaling, per the note at the top.

| query | requests | GraphQL points | scales with |
|---|---|---|---|
| Legacy topic-wide crawl (100 issues + 100 PRs per repo) | 1 per 100 repos, **search API (30/min)** | **202** | fleet |
| Discovery search + change tokens | 1 per 100 repos | 2 | fleet |
| `viewer.repositories` + topics + counts | 1 | **3** | user's own repos |
| Batched issues + PRs across a user's 26 repos | 1 | **14** | user's repos |
| Single repo | 1 | 1 | — |
| `GET /issues?filter=assigned&state=open` | 1 REST call | n/a | — |

Limits: core 5,000/hour, GraphQL 5,000 points/hour, **search 30/minute**. The search endpoint is the
strict one, and the legacy design depended on it.

`GET /issues?filter=assigned` is the important row. It is a real-time REST endpoint, not search-index
backed, and it answers the Annotate tab's question directly for both tiers at once — tested, 13 open
issues across 7 repos in one call, org and personal together.

Per-user fan-out across the 49 logins appearing in journals: **median 1 repo, mean 2.0, max 26.** This
is the number that does not scale with the fleet, and the one the design should lean on.

## Was this not the original design?

No. Work state was journaled from the first commit that created journals
([50cb005](https://github.com/MorphoDepot/RepoClerk/commit/50cb005), 2026-03-23) — `schemaVersion: 1`
already carried `openIssues` with assignees and `openPRs`. There was never a discovery-only phase.

The original README says why:

> The extension has Search, Annotate, and Review tabs that **previously queried the GitHub GraphQL
> API directly on every refresh** — causing latency and rate-limit issues with multiple concurrent
> users.

That was accurate, and caching all three was correct — because at the time all three *were*
fleet-wide. `issueList()` and `prList()` both call `ghTopicData()`, the topic-wide crawl, then filter
client-side for entries naming the current user. The extension answered "what am I assigned?" by
downloading the whole fleet and searching it locally.

So nothing was switched and no decision was skipped. What changed is that two of the three questions
can now be asked at user scope instead of fleet scope. The work state was expensive because of **how
it was fetched**, not because of what it is. This proposal is the original design plus a cheaper way
to ask two of its three questions.

## Shape

| | lives in | refreshed | scales with |
|---|---|---|---|
| **Discovery index** — species, modality, spacing, dimensions, screenshots, volume size, checksum, curator, collection membership | RepoClerk, **all repos** | push-gated, hourly | new repos and pushes |
| **Work state** — open issues, open PRs, draft status, assignees | live, per user (personal repos; org too under the extension) | on demand | one person's repos |
| **Dashboard, collections, releases, DOI** | RepoClerk, **org repos only** | as today | org fleet |

Per tab:

- **Search** — reads the index. Every repo present, personal included. No live API.
- **Annotate** — `GET /issues?filter=assigned`, filtered to `morphodepot`-topic repos. One real-time
  call, both tiers.
- **Review** — `viewer.repositories(ownerAffiliations: OWNER)` filtered by topic, plus one batched
  query for issues and PRs.
- **Release / Collections** — unchanged, org-only already.

### One place a tier distinction survives, stated plainly

**Work state has one source per repo, whichever variant is chosen.** No view reads the same repo's
issues from two places. That is the property worth having, and it is what the earlier tier-split
draft of this proposal failed.

But *which repos concern me* is not uniform in Review, and pretending otherwise would be
overclaiming. A curator's list is a union:

| | source | why |
|---|---|---|
| my own repos carrying the topic | live `viewer.repositories(ownerAffiliations: OWNER)` | I own them, so GitHub enumerates them directly |
| org repos where `CURATOR` names me | the index | the org owns them, so an owner-keyed query cannot find them — and `CURATOR` is a committed file, hence index data |

Two defenses, which readers should judge:

1. It is a *union*, not a *branch*. Both queries run and merge; nothing classifies a repo before
   deciding how to treat it.
2. The org half is index data by nature. `CURATOR` lives in a file in the repo, so it is push-gated
   and belongs in the index on the same grounds as species. Reading it there is the rule applied
   consistently, not an exception.

Annotate has no such asymmetry, and neither does Search. The Review repo-list is the only place a
tier distinction remains, and it exists because GitHub's ownership model makes "repos I curate but do
not own" unanswerable without stored state.

## What this removes

- **The 2026-08-07 failure class.** No journal entry for issues means nothing to go stale for personal
  repos. The classroom outage becomes structurally impossible rather than instrumented against.
- **#469 becomes deletable rather than fixable**, under either variant. `notifyRepoClerk()` exists to
  refresh journals after issue and PR events; with the index push-gated and the webhook already
  covering org repos, it has no remaining job — and its loop-termination blocker goes with it.
- **The tier gap stops being a defect.** The org webhook covers the tier that keeps a work-state
  cache. That alignment has been treated as a bug; under the narrow proposal it becomes the design.
- **Churn and full-fleet cost fall**, which is what buys headroom for the target scale.

## What it costs

- **A second artifact to keep coherent**, though they are independent by design.
- **Live calls fail when GitHub does.** The obvious version of this concern is wrong — there is no
  offline resilience to lose. `refreshRepoClerk()` returns `None` when `git pull` fails, and
  `ghTopicData()` and `morphoRepos()` then return `[]`, discarding journals already on disk. The
  docstring claims a "fallback to direct API" that does not exist. *This is extension-side code and
  should be confirmed there before this document is used to argue the point.*

  The split arguably improves the failure mode: a failed live call can say "could not reach GitHub,"
  whereas an empty journal-backed list is indistinguishable from "no work assigned to you" — the
  ambiguity behind SlicerMorph/SlicerMorphoDepot#212 and much of the 2026-08-07 investigation.
- **Per-user API budget**, measured above, with each client authenticating as its own user, so a class
  of thirty has thirty separate budgets.

## Meeting the one-hour requirement

The index is push-gated, so an hourly sweep satisfies it with the hour spare. The normal case is much
faster: publishing already runs through the intake App, so the App can dispatch an index refresh by
name at publish time — seconds, no webhook scope problem, no label, no tier distinction. The sweep
becomes the backstop for a failed dispatch, which is the correct role for a backstop.

At target scale, note that the sweep's *discovery* phase pages the topic search at 100 repos per
request, so it grows linearly in requests while staying far inside the search limit; it is the queue
and drain phases in §2 that need rework, not discovery.

## Open questions

1. **Is "discoverable but not advertised" the right policy** for personal repos? It is a visible
   change and should be a deliberate product decision. What are personal-repo owners told?
2. **What is the org tier for?** Not "how much is in it" — it is a week old and the answer would be an
   artifact of that. Is it the destination most datasets are expected to reach once they are finished,
   or a curated subset with the rest living permanently in personal accounts? The answer sets whether
   hundreds of org repos is a floor or a ceiling, and therefore how much the org-only presentation
   layer has to scale.
3. **Adopt the extension, or only the narrow proposal?** See the arguments above.
4. **How does a repo move between tiers?** A personal repo later published into the org needs its
   index entry and dashboard presence to follow it.
5. **Rework the queue and full-fleet path.** §2 says the issue-as-queue design and the serial drain do
   not survive the target scale, independently of this proposal. Batching, resumability, and a queue
   that is not GitHub issues are all implied. Does that happen alongside this or before it?
6. **Which of `near-realtime-ingestion.md`'s layers survive?**

   | layer | under this proposal |
   |---|---|
   | 1 — authoritative sweep (`activityAt`) | survives under the narrow version (org work state still journaled); lapses under the extension |
   | 2 — close the tier gap (App events) | lapses for work state; the index still wants a push signal, which the publish-time App dispatch covers |
   | 3 — make failure loud | **still required, unchanged.** A failed index drain is exactly as invisible as a failed work-state drain, and a stale index means a repo silently missing from Search — the same class of failure that went seven weeks unnoticed |
   | 4 — scale | **more urgent, not less.** §2 and §4 above are Layer 4 work restated against the real target |

7. **The extension's offline behavior** — is the one-line clone-path fallback part of this work or
   separate?

## Interim guidance for instructors

Until something here changes, the current behavior affects how a class should be run:

- Creating and assigning issues triggers **no** notification path at all, for anyone. It always waits
  for the sweep — currently up to 15 minutes.
- Publishing a repo does notify, but only for accounts with triage permission on RepoClerk (#469).
- A student opening a PR or clicking *Request review* is on the same path, so **the delay applies
  during class, not just to setup**.
- Publish the repo and create and assign every issue at least 20 minutes before the session. Sweeps
  run at `:08`, `:23`, `:38`, `:53`.
- An org owner can force it immediately with
  `gh workflow run sync-all.yml --repo MorphoDepot/RepoClerk`. Nobody without write access here can.

## Related

- `design/near-realtime-ingestion.md` — the freshness architecture and the 2026-08-07 incident
- #469 — label gate; deletable rather than fixable under either variant
- #470 / #475 — the `pushedAt` staleness bug and the `activityAt` watermark that fixed it
- #556 — the 15-minute sweep
- SlicerMorph/SlicerMorphoDepot#211, #212 — extension-side investigation and the empty-list ambiguity
