# Splitting the journal: discovery index vs work state

**Status: proposal, for discussion. Nothing here is implemented.**
Raised by @muratmaga on 2026-08-07, after a day spent fixing the freshness bugs recorded in
`near-realtime-ingestion.md`. This document is the discussion written down, with the measurements
that grounded it. It supersedes that file's *Open question: what belongs in the journal at all*.

## The proposal, in one paragraph

Take issues and pull requests out of RepoClerk entirely and fetch them live from GitHub, per user,
at the moment they are needed. Keep everything else — species, modality, spacing, dimensions,
screenshots, volume size and checksum, curator — in a journal that all repos still appear in,
personal and org alike, refreshed periodically. Personal repos stay **discoverable** through Search;
they stop being **advertised** on the dashboard, which becomes the visible benefit of publishing into
the org.

The requirement set alongside it: **a repo must be findable within one hour of being published.**

## What prompted it

On 2026-08-07 a class stalled because five annotators could not see the issues assigned to them. The
investigation found three independent faults, all documented in `near-realtime-ingestion.md`: the
push path is an org webhook and does not reach personal repos; `notifyRepoClerk()` is a silent no-op
for non-collaborators (#469); and the hourly sweep compared `pushedAt`, which no issue event moves
(#470, fixed in #475).

Every one of those failures was about **issues and pull requests on personal repos**. None was about
species or volume size. That is the observation the proposal is built on.

## Measurements

All taken on 2026-08-07 against the live fleet.

### The fleet is lopsided

| tier | repos | open issues | open PRs |
|---|---|---|---|
| MorphoDepot org | 12 | 6 | 1 |
| personal accounts | **57** | **116** | **74** |
| test / scratch orgs | 11 | 13 | 2 |

95% of open issues and 99% of open pull requests are in personal repos. The cache serves its most
elaborate machinery — webhook, dispatch, drain, dashboard — to the tier where almost nothing happens.

### The two kinds of data move at completely different speeds

Everything the Search tab reads (`widget_search.py:72-135` — species, modality, spacing, dimensions,
size, captions) comes from files committed inside the repo. It cannot change without a push.

| how recently the repo **content** was pushed | repos |
|---|---|
| < 1 day | 1 |
| 1–7 days | 1 |
| 7–30 days | 13 |
| 30–90 days | 8 |
| **> 90 days** | **57** |

| how recently **issue/PR activity** happened | repos |
|---|---|
| < 1 day | 3 |
| 1–7 days | 6 |
| 7–30 days | 2 |
| 30–90 days | 8 |
| > 90 days | 40 |
| never | 21 |

Two repos saw a push in the last week. Nine saw issue or PR activity. One artifact currently carries
both, and the slower update frequency set the refresh policy for the faster one.

### Work state is cheap to fetch live; discovery is not

| query | requests | GraphQL points |
|---|---|---|
| Legacy topic-wide crawl, 80 repos × 100 issues + 100 PRs | 1/page, **search API (30/min)** | **202** |
| Discovery search + activity watermarks, all 80 repos | 1 | 2 |
| `viewer.repositories` + topics + issue/PR counts (a curator's own repos) | 1 | **3** |
| Batched issues + PRs for the heaviest real user's 26 repos | 1 | **14** |
| Single repo | 1 | 1 |
| `GET /issues?filter=assigned&state=open` — every issue assigned to me, anywhere | 1 REST call | n/a |

Limits: core 5,000/hour, GraphQL 5,000 points/hour, **search 30/minute**. The search endpoint is the
strict one, and it is the one the legacy design depended on.

`GET /issues?filter=assigned` is the important entry in that table. It is a real-time REST endpoint,
not search-index backed, and it answers the Annotate tab's question directly. Tested: 13 open issues
across 7 repos in one call, including the personal repo from the failed class.

### Per-user fan-out is small and does not grow with the fleet

Across all 80 journals, counting every login with an assigned issue, an authored PR, or a CURATOR
entry: **49 users, median 1 repo, mean 2.0, max 26.** Thirty-seven of the 49 touch exactly one repo.
The 26 is the project lead.

This is the crux. Legacy cost scaled with **fleet size** — 202 points at 80 repos, roughly 1,250 at
500, four refreshes an hour before lockout. Live per-user cost scales with **how many repos a person
actually works on**, which does not move when the fleet grows.

## Was this not the original design?

No. Work state was in the journal from the first commit that created journals
([50cb005](https://github.com/MorphoDepot/RepoClerk/commit/50cb005), 2026-03-23): `schemaVersion: 1`
already carried `openIssues` with assignees and `openPRs`. There was never a discovery-only phase.

The original README says why:

> The extension has Search, Annotate, and Review tabs that **previously queried the GitHub GraphQL
> API directly on every refresh** — causing latency and rate-limit issues with multiple concurrent
> users.

That was accurate, and caching all three was the right call — because at the time all three *were*
fleet-wide. `issueList()` and `prList()` both call `ghTopicData()`, the topic-wide crawl, and then
filter client-side for entries naming the current user. The extension answered "what am I assigned?"
by downloading the whole fleet and searching it locally.

So nothing was switched and no decision was skipped. What changed is that two of the three questions
can now be asked directly. The work state was expensive because of **how it was fetched**, not
because of what it is. This proposal is therefore not a reversal of the original design — it is the
original design, plus a cheaper way to ask two of its three questions.

## The proposal

| | lives in | refreshed | why |
|---|---|---|---|
| **Discovery index** — species, modality, spacing, dimensions, screenshots, volume size, checksum, curator, collection membership | RepoClerk, **all repos** | push-gated, hourly | changes ~twice a week, expensive to gather, shared by everyone |
| **Work state** — open issues, open PRs, draft status, assignees | nowhere; fetched live per user | on demand | changes constantly, cheap to fetch, only ever needed for a handful of repos |
| **Dashboard, collections, releases, DOI** | RepoClerk, **org repos only** | as today | the visible differentiator |

Per tab, after the split:

- **Search** — reads the index. Every repo present, personal included. No live API at all.
- **Annotate** — `GET /issues?filter=assigned`, filtered to `morphodepot`-topic repos. One real-time
  call. No cache, no staleness, no notify.
- **Review** — `viewer.repositories(ownerAffiliations: OWNER)` filtered by topic, plus one batched
  query for issues and PRs. 3 points, real-time. For in-org repos the journaled `curator` field
  still supplies "repos I curate", since the org owns them rather than the member.
- **Release / Collections** — unchanged, org-only already.

Note there is no source-switching in the client. Each *view* has exactly one source, and every repo
behaves identically. `near-realtime-ingestion.md` records a rejected alternative — special-casing an
"active repo" in the client — and the objection to it was that it scatters source selection through
the client and teaches it to distrust the cache. Splitting by data category rather than by repo does
not have that shape. An earlier draft of this proposal split by *tier* (org cached, personal live),
which did have that shape, and was dropped for that reason.

## What this removes

- **The entire failure class from 2026-08-07.** No journal entry for issues means nothing to go
  stale. The classroom outage becomes structurally impossible rather than instrumented against.
- **#469 evaporates.** `notifyRepoClerk()` exists to refresh journals after issue and PR events. With
  work state gone and the index push-gated, it has no remaining job — delete it rather than fix the
  label gate and its loop-termination blocker.
- **The tier gap stops being a defect.** The org webhook covers the tier that keeps a cache. That
  alignment has been treated as a bug; it becomes the design.
- **Writer contention and git churn collapse.** 57 repos producing no commits for months means the
  repository every client clones goes nearly static, which also retires the `REPOCLERK_SIZE_LIMIT_MB`
  guard and most of `near-realtime-ingestion.md`'s Layer 4.
- **The sweep can return to hourly** and still meet the one-hour requirement with the full hour as
  margin. The 15-minute cadence set in #556 exists because work state is currently journaled.

Honest consequence: **the `activityAt` watermark shipped in #475 becomes unnecessary under this
design.** It exists to tell a sweep when issue activity happened; if issues are not journaled, it has
no consumer. It was the right fix for the current architecture and should stay until this lands. Its
durable output is the finding that `Repository.updatedAt` does not move on issue activity, which
would have cost the next person a day.

## What it costs

- **A second artifact to keep coherent.** They are independent, which is the point, but it is two
  things rather than one.
- **Fleet-wide duplicate-volume detection** keeps working (checksum is index data), but any
  cross-repo analysis of *issues* would no longer have a corpus to run against. Nothing does that
  today.
- **Live calls fail when GitHub does.** Worth stating precisely, because the obvious version of this
  concern is wrong: there is no offline resilience today to lose. `refreshRepoClerk()` returns `None`
  when `git pull` fails, and `ghTopicData()` and `morphoRepos()` then return `[]` — the journals
  already on disk are discarded exactly when they would be most useful. The docstring claims a
  "fallback to direct API" that does not exist.

  The split arguably *improves* this. A failed live call can say "could not reach GitHub"; an empty
  journal-backed list says nothing, and is indistinguishable from "no work assigned to you" — the
  ambiguity that made the 2026-08-07 investigation take a full day, and the same complaint as
  SlicerMorph/SlicerMorphoDepot#212. A one-line fix to return the existing clone path on pull failure
  is worth doing regardless of this proposal.
- **Per-user API budget**, measured above at 1–14 points per refresh against 5,000/hour, with each
  client authenticating as its own user so a class of thirty has thirty separate budgets.

## Meeting the one-hour requirement

The index is push-gated and pushes are rare, so an hourly sweep satisfies it with the whole hour
spare. The normal case is much faster: publishing already runs through the intake App, so the App can
dispatch an index refresh by name at publish time — seconds, no webhook scope problem, no label, no
org-versus-personal distinction. The sweep becomes the backstop for a failed dispatch, which is the
correct role for a backstop.

One caveat: `sync-all` discovers repos through GitHub's topic search, which is index-backed and lags
minutes. Within an hour that is comfortable, and the publish-time dispatch bypasses it by naming the
repo directly.

## Open questions

1. **Is "discoverable but not advertised" the right policy** for personal repos? It is a visible
   change — the dashboard drops from 80 tiles to 12 — and it should be a deliberate product decision,
   not a side effect. What are owners of the 57 personal repos told?
2. **Is the org tier the destination most data reaches, or a small curated shelf?** If the first, this
   is clearly right. If the second, RepoClerk runs a webhook, a drain, and a dashboard for twelve
   repos holding six issues.
3. **How does a repo move between tiers?** A personal repo later published into the org needs its
   index entry and dashboard presence to follow it.
4. **What happens to the extension's offline behaviour** — is the one-line clone-path fallback part of
   this work or separate?
5. **Sequencing.** #469 and the Layer 2 App-permission decision both become moot if this lands. Doing
   either first is wasted effort; doing neither leaves known bugs open while this is discussed.

## Interim guidance for instructors

Until something here changes, the current behaviour is worth stating plainly, because it affects how
a class should be run:

- Creating and assigning issues triggers **no** notification path at all, for anyone. It always waits
  for the sweep — currently up to 15 minutes.
- Publishing a repo does notify, but that only works for accounts with triage permission on RepoClerk
  (#469). For everyone else it is the sweep again.
- A student opening a PR or clicking *Request review* is on the same path, so **the delay applies
  during class, not just to setup**.
- Practical advice: publish the repo and create and assign every issue at least 20 minutes before the
  session. Sweeps run at `:08`, `:23`, `:38`, `:53`.
- An org owner can force it immediately with
  `gh workflow run sync-all.yml --repo MorphoDepot/RepoClerk`. Nobody without write access here can.

## Related

- `design/near-realtime-ingestion.md` — the freshness architecture, the 2026-08-07 incident, and the
  four-layer plan this proposal partly supersedes
- #469 — label gate; would be deleted rather than fixed
- #470 / #475 — the `pushedAt` staleness bug and the `activityAt` watermark that fixed it
- #556 — the 15-minute sweep, which could return to hourly under this proposal
- SlicerMorph/SlicerMorphoDepot#211, #212 — the extension-side investigation and the empty-list
  ambiguity
