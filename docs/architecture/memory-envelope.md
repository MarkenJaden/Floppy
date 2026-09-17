# Memory envelope

Floppy's ordinary footprint is already small. What is not yet bounded is its
**high water** — the size it reaches briefly, during a routine job, and then
gives back.

A container that settles below 800 MiB is not honestly a "under 1 GB
application" if a nightly backup can take its cgroup to 5 GB. An operator
running Floppy under a 1 GB limit needs it to boot, back up and serve inside
that limit, not on average.

## Two envelopes, deliberately separate

Reporting one number would be misleading, because two different things grow
and they have to be fixed differently.

**Application resident envelope** — long-lived process PSS and private
anonymous memory. This is what Python allocated: object graphs, caches,
fragmentation. It is not reclaimable under pressure; the kernel's only remedy
is to kill the process.

**Operational cgroup envelope** — everything charged to the container,
including kernel memory and the filesystem page cache. It is mostly
reclaimable, but reclaimable is not free: a 1 GB cgroup limit is still a
limit, and a job that needs 3 GB of page cache to finish inside a 1 GB cgroup
spends the difference thrashing.

So "reclaimable" is never an excuse for a raw peak, and a small process PSS is
never on its own proof of a small container.

Long-term targets, against the *aged application* envelope:

| Target | Standing |
| --- | --- |
| < 750 MiB aged warm container | immediate |
| < 600 MiB | near-term |
| < 500 MiB aged application footprint | primary |
| < 400 MiB | strong stretch |
| < 300 MiB | architecture territory |
| < 200 MiB | major redesign territory |

Neither envelope has a committed ceiling yet. Choosing one needs measurements
this repository cannot take; see [Docker validation plan](#docker-validation-plan).

## Instrumentation: `app/memory_envelope.py`

Until now the only evidence of an excursion was a Portainer graph correlated
against timestamps by hand, which cannot say whether the memory was Python's
or the page cache's. The envelope module samples both at request and task
boundaries.

What it reads, all of it cheap:

| Field | Source |
| --- | --- |
| `rss` | `/proc/self/statm` field 2 |
| `hwm` | `/proc/self/status`, `VmHWM` |
| `cgroup_current` | `/sys/fs/cgroup/memory.current` |
| `anon`, `file`, `kernel` | `/sys/fs/cgroup/memory.stat` |
| `cgroup_peak` | `/sys/fs/cgroup/memory.peak` |

`VmHWM` is the field that makes a *freed* excursion visible: a request that
builds a 1.5 GiB object graph and releases it before returning leaves RSS
almost unchanged at both boundaries, and VmHWM keeps the mark.

No `smaps`, and no PSS. PSS costs a full VMA traversal per sample and stays
where it belongs, in the diagnostic sampler
(`scripts/container_memory_sample.py`). What is here measured at **39 µs a
sample**, so roughly 80 µs a boundary — cheap enough to leave enabled, which is
the point: the sample that explains a 5 GB spike is the one that was already
running when it happened.

cgroup v2 first. Every probe degrades independently to `unknown` rather than to
zero, so a cgroup v1 host still reports process RSS and VmHWM, and a host with
no `/proc` at all still serves requests. Reporting is wrapped so that
instrumentation can never turn a 200 into a 500.

### When it logs

Nothing for an ordinary boundary. A structured `memory_high_water` event fires
only when one of these crosses its threshold:

| Reason | Setting | Default |
| --- | --- | --- |
| `duration` | `MEMORY_HIGH_WATER_DURATION_MS` | 10 000 |
| `rss_growth` | `MEMORY_HIGH_WATER_RSS_DELTA_BYTES` | 32 MiB |
| `peak_rss` (new VmHWM) | `MEMORY_HIGH_WATER_HWM_DELTA_BYTES` | 32 MiB |
| `cgroup_growth` | `MEMORY_HIGH_WATER_CGROUP_DELTA_BYTES` | 128 MiB |
| `page_cache_growth` | `MEMORY_HIGH_WATER_CGROUP_FILE_DELTA_BYTES` | 128 MiB |
| `near_recycle_ceiling` | `MEMORY_HIGH_WATER_CEILING_RATIO` | 0.85 |

Zero disables a signal. `MEMORY_HIGH_WATER_ENABLED=False` disables the whole
layer, including the sampling.

These are starting points, chosen to catch the known production events, and
are expected to be tuned once real events accumulate. **None of them is a
memory guarantee.**

The event carries `kind`, `name`, `pid`, `role`, `reasons`, `duration_ms`,
`rss_before/after/delta`, `hwm_before/after`, `cgroup_before/after/delta`,
`anon_before/after`, `file_before/after`, `kernel_after`, `cgroup_peak` and the
role's recycle `ceiling`.

### What it will not log

Request names are the resolved route with its captured parameters substituted
back in — `/medialist/movie`, not `medialist/<str:media_type>`, because "which
list blew up" is the question these events exist to answer. Never the query
string, and never a parameter whose name says it carries a credential in the
path (`key`, `token`, `uidb36`, `sid`, `signature`, …); those are redacted by
name. An unresolved request logs `<unresolved>` rather than falling back to the
raw URL.

Celery events carry the task name and its opaque task id, never its arguments:
arguments carry user ids, search terms and credentials.

### Reading an event

`rss_growth` or `peak_rss` without `page_cache_growth` is **process-anonymous
growth** — Python built something. `page_cache_growth` with `anon` flat is
**filesystem cache** — something wrote or read a large file. Both together
usually means a large read into Python. `duration` alone means slow but not
large, which is a responsiveness problem rather than a memory one.

## Known high-water sources

| Source | Kind | Status |
| --- | --- | --- |
| Database snapshot | page cache (hypothesis) | released; needs Docker to confirm |
| `/medialist/movie` | process-anonymous | structurally bounded |
| Pocket Casts recurring poll | process-anonymous + duration | convergence fixed, counters added |
| Statistics restart thrash | duration / worker occupancy | settling window added |
| Statistics FINISH | process-anonymous | **unfixed**, documented below |
| History day-cache warming | process-anonymous | **uninvestigated** |

### Database snapshot — the largest raw excursion

At 02:30 the cgroup rose from roughly 1–2 GB to above 5 GB alongside about
2 GB of write I/O, then decayed over hours. Decay over hours is what
reclaimable page cache does; it is not what a Python leak does.

The write path supports that reading. `sqlite3.Connection.backup()` copies the
whole database through ordinary buffered I/O, and `PRAGMA quick_check` then
reads every page of the copy back — so one snapshot charges the container
roughly **twice the database's size** in the cgroup's `file` accounting, for a
file Floppy will not read again until the live database is unreadable.

**This remains a hypothesis.** Nothing sampled `memory.stat` at the event. The
`db_snapshot` log line added alongside the fix is what will settle it.

The fix issues `POSIX_FADV_DONTNEED` on the finished snapshot. Its safety
comes from ordering: the hint is issued strictly *after* `fsync` and while the
descriptor is still open. `DONTNEED` drops only clean pages, so it can never
cost durability however it is timed, and after `fsync` there are none to skip.
The live database is never hinted — its cache is doing useful work. The file
itself is untouched: no truncate, no unlink, only a hint about the cache in
front of it. `posix_fadvise` is feature-detected; a platform without it still
publishes, and a hint that fails is logged and ignored. Global `drop_caches` is
never used.

The default snapshot minute also moved from `:30` to `:37`. The incremental
metadata backfill runs at `*/15` or `*/30` depending on tier, so the old
default started a whole-database copy in the same minute as a bulk sweep —
exactly the pairing production logged. An operator who has set
`DB_SNAPSHOT_MINUTE` keeps their own value.

### `/medialist/movie` — 110–125 s across 11 queries

Eleven queries is not an N+1 problem. It is a few queries each returning far
more than the page needs, which is why a query-count budget stayed green
throughout.

`_aggregate_duplicate_data` filters by *item id*, not by page, so on a list
that is not paginated in SQL it returns a row for every tracked title. It
fetched them with `select_related("item")` and no deferral, hydrating a full
`Item` each — including `synopsis` and the `watch_providers` blob, roughly
146 KiB a title, that every surrounding queryset is careful to defer. One SQL
query, hundreds of MiB of JSON decoding.

It now projects the eight scalar columns the aggregation reads and joins
`app_item` not at all. It also dropped `Media`'s default ordering
(`["user", "item", "-created_at"]`), which made the database join `users_user`
and `app_item` purely to sort a result set that is immediately grouped into a
dict by item id.

Separately, the separate-entries ("show each play") list carried its own copy
of the deferred-field list, and the copy had drifted: it no longer deferred
`item__watch_providers`, so that mode loaded the blob for the whole library.
The copy is deleted and the one definition imported.

Note what makes a request take the non-SQL-paginated path at all — it is easy
to fall onto and hard to notice: a non-empty `pinned_watch_providers`, a
persisted `no_status` filter, `movie_show_each_play`, or a sort of `runtime` or
`time_watched`.

### Pocket Casts — 1000 s every two hours, importing nothing

`synced=5237 skipped=401` could not distinguish 5237 rows written from 5237
rows inspected and left alone. The counters are now `examined`, `unchanged`,
`changed`, `created`, `written`, `hydrated`, and `changed > 0` with
`written = 0` is the readable signature of a rewrite loop.

One such loop is fixed: the freshness check compared the raw provider value
against a stored one the database had already coerced, so a duration the
provider does not send as a plain `int` (`"1800"`, `1800.0`) differed forever —
write, normalise, differ, write. Whether that is what production is hitting is
**not established**; there is no recording of the live wire format in the repo.
The counters are what the next run should be read for.

Two bounds alongside it: the end-of-run duplicate sweep no longer hydrates
every `PodcastEpisode` to discover a clean catalog has no duplicates, and
`episode_uuid` has an index (`unique_together` leads with `show_id`, so the
per-episode lookup by uuid alone was a table scan).

### Statistics restart thrash

A run that aborts on `history_version_changed` used to restart immediately.
Under a credits backfill — which bumps the version roughly every ten seconds
while its queue drains — that produced seven aborted All Time refreshes in a
minute. An abort of that kind now waits a short settling window that coalesces
further aborts. See [statistics-refresh-runs.md](statistics-refresh-runs.md)
for the state machine itself.

## Still unverified, and still unfixed

**The snapshot page-cache hypothesis.** Plausible from the write path and the
decay shape; not measured. The `db_snapshot` line settles it.

**Whether `/medialist/movie` now fits in a worker.** The object graph is
structurally smaller. Whether the request stops approaching the 400 MiB
recycle ceiling is a Docker measurement.

**Statistics FINISH, 30–53 s.** `_aggregate_statistics_from_days` is a single
~1800-line function with about thirty nested accumulators that grow with the
number of *distinct items* in the range, not with days. Its 50-day
`get_many` loop is already batched; the cost is after it — per-media-type
undated-row sweeps, four separate `_fetch_media_objects` passes, the top-talent
credit rollup, and `_get_history_day_payload` falling through to a full inline
`history_cache.build_history_day` twice per active media type. Deliberately not
touched: #1200's own design document already names FINISH as the unbounded
remainder, and reshaping it without a profile would be guessing. **Profile it
first**, with the per-phase timings the aggregator does not currently emit.

**History day-cache warming.** Production repeatedly spends ~18–22 s rebuilding
120 session-style days and ~48–50 s rebuilding 120 repeats-style days against
histories with several thousand days of coverage. Not investigated this
session. The questions to answer before changing anything: why thousands of
historical days are eagerly warmed at all; which user-visible paths actually
require full coverage rather than recent coverage; whether repairs are
duplicated across schedulers and could be coalesced; whether 120 is the right
fixed batch; and whether repair should back off while a major background job
is running.

**Gunicorn concurrency.** Production sets `WEB_CONCURRENCY=2` explicitly even
though the runtime would choose one worker on that host, and each extra worker
holds its own resident copy of the application. Deliberately not changed here:
the trade needs Docker, not reasoning.

## Docker validation plan

This session cannot prove a ceiling. Nothing below has been measured.

Read `memory_high_water` and `db_snapshot` lines from the container log
throughout; where a step says "sample", use
`scripts/container_memory_sample.py`, which reports PSS per process alongside
the cgroup.

### 1. Database snapshot

Sample cgroup `current`, `file` and `anon` immediately before, during,
immediately after, then at **+30 s, +60 s and +5 min**.

- Confirm `file` is what rises, and by roughly twice the database size if the
  hint is not working.
- Confirm the `db_snapshot` line's `file_after - file_before` matches.
- Confirm `page_cache_release ... advice=issued` appears, and that `file` after
  the snapshot is close to `file` before it.
- Run once with `os.posix_fadvise` unavailable to confirm the fallback still
  publishes a valid snapshot.

### 2. `/medialist/movie`

Against a large library, with a user configured to take the **non-SQL-paginated
path** (pin a watch provider, or sort by `runtime`) — otherwise the fast path
hides the regression this targets.

Record worker RSS, PSS and private-anon **before, at peak, after, and once
settled**. Confirm the worker does not reach the 400 MiB recycle ceiling and
is not retired. Compare against `3fe29eff`.

### 3. Pocket Casts

Across a full no-op recurring run: background child memory and duration, and
the `examined / unchanged / changed / created / written / hydrated` counts.

A converged run is `unchanged ≈ examined` with `written = 0`. If `changed` is
high while `written` is 0, another field has the same representation mismatch
duration had — the counters now name the failure rather than hiding it.

### 4. Statistics

Run a large rebuild while repeatedly injecting Plex/Stremio webhooks. Record
max chunk duration, FINISH duration, webhook wait time, the number of aborted
runs, and the number of follow-ups scheduled. Expect follow-ups to be far
fewer than aborts — that is the coalescing working.

### 5. Aged run

Run the production-like workload for several hours. Verify that both process
PSS and raw cgroup memory return to a repeatable envelope rather than
ratcheting. Only after this should `X` and `Y` be chosen.

## Reporting the result

When the measurements exist, report **two** numbers, never one:

- **Application resident ceiling** — process PSS / private-anon after aged
  workloads.
- **Operational cgroup ceiling** — raw cgroup memory including filesystem
  cache during supported routine jobs.

"Floppy normally settles below X, and routine supported workloads remain below
Y."
