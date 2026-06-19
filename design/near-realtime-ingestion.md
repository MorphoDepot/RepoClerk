# Near-real-time RepoClerk ingestion (for live workshops)

**Status:** design proposal for review (@pieper). Not yet implemented.
**Latency target:** ≤ 2–3 minutes end-to-end (an instructor's action → visible in attendees' extension). Sub-second is *not* required.

## The concern: live workshops are the one real-time case

MorphoDepot usage is overwhelmingly **asynchronous** — a PI publishes a repo, segmentors work over
days or weeks. There, RepoClerk's eventual consistency (cache refreshed within ~30 s on a clean
notify, ≤ 1 h via the cron backstop) is invisible and completely fine.

The one scenario where freshness matters is a **live workshop / classroom**: an instructor creates a
repository on the spot and asks attendees to create issues and start segmenting *immediately*. In
that burst, attendees need the new repo (Search/discovery), their new issues (Annotate tab), and
their PRs (Review tab) to appear within a couple of minutes — otherwise people sit staring at empty
lists during the session. This is the case worth engineering for, and ≤ 2–3 minutes is plenty.

## Why it lags today (the real cause: pull-shaped ingestion)

The extension's list views read RepoClerk's pre-computed journals **by design** — to avoid
hammering GitHub with topic-wide searches as the repo count grows (the whole reason RepoClerk
exists). Those journals are only as fresh as the last crawl, and crawling is triggered by a
**pull/poll** mechanism:

- `notifyRepoClerk` opens an `update-request` **issue** (GitHub issues used as a message queue),
  which `update-repo.yml` consumes;
- a `sync-all` cron sweep (now hourly) as a backstop;
- and the `repoclerk-writer` concurrency lock can **drop** a triggered run instead of queuing it
  (observed in testing: an `update-request` issue was *skipped*, so that refresh waited for the
  cron).

So the latency and the unreliability live entirely in the **trigger** mechanism — not in the read
path, which is fine and should stay the single, uniform source.

## Rejected alternative: client-side source switching

A tempting quick fix is to special-case the active/workshop repo in the **extension** — roughly
`if active_repo: query GitHub live; else: read RepoClerk`. We reject this: it's a band-aid that
scatters source-selection logic through the client, teaches the client to distrust the cache, needs
a per-repo "active repo" field, and only helps the one hand-entered repo. The right fix makes the
cache *correct/fresh*, so the read path stays uniform and untouched.

## Proposed design: push-shaped ingestion via GitHub webhooks

Replace the poll/issue-queue trigger with a GitHub **org webhook**, so every change is pushed to
RepoClerk within seconds, reliably, for **all** repos and event types — with **no extension change**.

1. **Org webhook** on `MorphoDepot` for `repository`, `issues`, `pull_request`, `release`, `push`.
   GitHub delivers each event to an HTTPS endpoint within ~1 s, with automatic delivery retries.
2. **Receiver = the intake app** (`morphodepot-intake`, already an always-on FastAPI service on JS2
   that holds the GitHub App credentials). Add one endpoint, `POST /github/webhook`, that:
   - verifies the `X-Hub-Signature-256` HMAC against a shared secret;
   - extracts the affected repo's `nameWithOwner`;
   - triggers a journal refresh for that repo via **`repository_dispatch`** (`event_type:
     update-repo`) to `MorphoDepot/RepoClerk` — which `update-repo.yml` already accepts.
3. **Coalesce** events per-repo over a short window (≈ 5–10 s) so a burst (30 attendees acting at
   once) collapses into one drain instead of 30.

**End-to-end latency:** webhook delivery (~1 s) + dispatch + the existing drain Action (~25–30 s) ≈
**well under a minute** — comfortably inside the 2–3 min target.

## What this buys

- **No extension change.** `issueList` / `prList` / Search keep reading RepoClerk uniformly — no
  active-repo field, no `if/then` source switch.
- **Uniform freshness (~seconds) for every repo and every event**, not just a workshop repo.
- **Eliminates two kludges at once:** the issue-as-message-queue *and* the concurrency-skip
  fragility — push + coalesce + GitHub's delivery retries replace poll + drop + cron-catch-up.
- **Cron stays as the safety net** (hourly) for any missed delivery.

## Components / work

| Where | Change |
|---|---|
| **intake app** | new `POST /github/webhook` (HMAC verify + per-repo coalesce + `repository_dispatch`); a `GITHUB_WEBHOOK_SECRET` in the env |
| **RepoClerk** | none required — `update-repo.yml` already accepts `repository_dispatch`. Optionally retire the `notifyRepoClerk` issue-queue once webhooks are proven (keep the cron backstop) |
| **org (one-time, owner)** | add the org webhook: URL = the intake app, the shared secret, the 5 event types |
| **extension** | **none** |

## Tradeoffs

- Adds a webhook secret + signature verification, and a one-time org-admin step (org owner).
- The intake server becomes load-bearing for freshness — but it already owns upload, the control
  plane, and the GC, so this is consistent with its role, not new attack surface.
- Near-real-time, not instant: the drain still runs as an Action (~30 s). If sub-second were ever
  needed, the receiver could write the journal *directly* (it has the App token + boto3), at the
  cost of a second journaling code path — unnecessary for the ≤ 3 min target.
