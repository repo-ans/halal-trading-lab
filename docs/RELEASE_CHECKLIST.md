# Release Checklist — Stage H

Read `CLAUDE.md` first. This file is the living record of hardening/release
-readiness work (CLAUDE.md section 9's must-pass tests, section 10's Stage H row).
Last updated: 20 Nov 2026, after Stage H's security-hardening pass, a clean-clone
test, an independent review, and the fixes that review's findings required.

## 0. Independent review (20 Nov 2026)

A fresh agent with no context from this build reviewed the committed Stage H
code directly (not this document's claims about it). Confirmed-safe: the
allowlist covers every authenticated route with no bypass, the fail-closed
`_user_id_set` properties really do produce an empty set from an empty string
(not `{""}`), the JWT `require`/`issuer` checks are genuinely shared by both
verification branches, `EngineCurrencyMismatch` is a real raise that survives
`python -O`, `validate_evidence_url`'s host check reaches both the route and the
CSV importer, and the rate limiter has no bypass path. **Found one real gap**:
the licence guard only covered `GET /instruments/{id}/bars` -- `GET /watchlist`
and the entire `/strategy-runs/*` surface leaked the same yfinance-derived data
to any allowlisted non-owner user. Fixed the same day; see section 1's licence
-guard entry below for the full writeup.

---

## 1. What is done

### Access control (new, 20 Nov 2026)
- `ALLOWED_USER_IDS` (env, comma-separated Supabase user UUIDs). Empty/unset means
  NO ONE is allowed -- fail closed, same shape as `ADMIN_USER_IDS`.
  `app/auth.py::current_user` rejects a valid, correctly-signed JWT for a user not
  in this set with **403**, for every single authenticated route -- there is no
  second choke point to keep in sync, since every route already goes through
  `current_user` (directly, or via `require_admin`). `ADMIN_USER_IDS` members count
  as allowed automatically.
- README now tells the owner to also disable public sign-ups in the Supabase
  dashboard (Authentication > Providers) -- `ALLOWED_USER_IDS` stops an
  unauthorized user from calling the API, not from obtaining a valid JWT in the
  first place.
- Tests: `backend/tests/test_access_allowlist.py` (5 tests) -- real HTTP requests
  with real, locally-signed JWTs (not a dependency override, which would bypass
  this exact logic). Covers: not-allowed -> 403 on both a GET and a POST route,
  allowed -> 200, admin-not-in-allowlist -> 200 (admin counts as allowed), and the
  fail-closed empty-everything case.

### Licence guard (new, 20 Nov 2026 -- interim, CLAUDE.md section 3 point 5;
### corrected same day after an independent review found it incomplete)
- `OWNER_USER_IDS` (env, comma-separated UUIDs, fail-closed). First version only
  gated `GET /instruments/{id}/bars` (**503** instead of the requested bars if
  any row has `source='yfinance'` and the caller isn't in `OWNER_USER_IDS` --
  never a partial/filtered response, CLAUDE.md's "never invent or silently omit
  data" principle, same reasoning as must-pass test 6).
- **An independent review caught a real gap the same day**: that was the ONLY
  route gated. `GET /watchlist`'s `latest_close` and the entire `/strategy-runs/*`
  surface (any allowlisted user could backtest a yfinance-sourced instrument and
  reconstruct most of its price series from the signals/fills/metrics/CSV) leaked
  the same data with zero check. Both fixed same day:
  - `GET /watchlist`: `latest_close` null + new `latest_close_restricted: true`
    for a yfinance-sourced instrument shown to a non-owner (a bare null would be
    indistinguishable from "no data", its own silent omission).
  - `POST /strategy-runs`: `run_strategy`'s new `caller_is_owner` parameter
    (**default `False`, fail-closed**) blocks a yfinance-sourced instrument for
    a non-owner with reason `"licence_restricted"` in the same `blocked_instruments`
    dict the compliance gate already populates -- the instrument never enters
    the run, so nothing downstream has to separately remember to check.
    `replay_strategy_run` needed no change: it already copies `blocked_instruments`
    verbatim instead of re-gating.
- Deliberately NOT the Supabase Admin API-based design sketched in the Stage H
  plan ("how many real Supabase users exist") -- that would need a service-role
  key in the backend. This is simpler and keeps that key out entirely. It is an
  interim measure, not a substitute for the real fix: deleting every
  `source='yfinance'` row once a licensed provider is backfilled (see section 2).
- Tests: `backend/tests/test_licence_guard.py` (5, the bars route),
  `backend/tests/test_watchlist_route.py` (4 new: non-owner restricted, owner
  sees it, non-yfinance never restricted, "no data" vs. "restricted" are
  distinguishable), `backend/tests/test_strategy_run_service.py` (3 new: blocked,
  owner not blocked, default-is-not-owner), `backend/tests/test_strategy_runs_route.py`
  (2 new: same two cases through the real route, proving `is_owner(user)` is
  actually wired through end to end).

### JWT verification
- Found already correct before Stage H: expiry, audience, and algorithm are
  enforced by PyJWT's own `algorithms=`/`audience=` machinery (`alg=none` cannot
  pass since "none" is never in the allowed list; a tampered signature fails
  PyJWT's own verification).
- Added in Stage H: **issuer (`iss`) validation**, which was not checked at all
  before. `app/auth.py::_expected_issuer` derives `"<SUPABASE_URL>/auth/v1"` and
  passes it to `jwt.decode(..., issuer=...)` on both the legacy-HS256 and the
  JWKS verification paths.
- Tests: `backend/tests/test_jwt_security.py` (7 tests) -- expired, wrong
  audience, wrong issuer, `alg=none`, tampered signature, tampered payload (same
  signature, swapped payload), and one positive control (a fully valid token is
  accepted).

### Rate limiting
- `app/rate_limit.py::SlidingWindowLimiter`, in-process, per-user, applied to
  `POST /strategy-runs` and `POST /strategy-runs/{id}/paper-run` only (the two
  uncapped, DB/compute-heavy routes -- `ai-summary` already has its own daily cap
  from Stage G). Limits (10 requests / 60s per route per user) are a placeholder,
  not a researched figure, same caveat CLAUDE.md section 11 already applies to
  the Stage E cost-assumption defaults.
- **Single-instance limitation, stated plainly**: state is in-process memory, not
  a shared store. More than one backend instance would give each instance its
  own independent counter. Documented in README and CLAUDE.md section 12. Fine
  today (Render free tier, one container).
- Tests: `backend/tests/test_rate_limit.py` (5 tests) -- 3 pure unit tests on the
  limiter itself (blocks at the limit, independent keys, old hits roll off the
  window) plus 2 route-level HTTP tests proving both routes actually call it.

### Error-response safety
- No custom exception handler exists in `app/main.py` (confirmed by reading it) --
  an unhandled exception falls through to Starlette's default, which was not
  previously under test.
- Added a real test that forces a genuine unhandled exception (not a mock of the
  response) and asserts the body contains no traceback, no file path, no SQL
  text, and not even the original exception's own message.
- Test: `backend/tests/test_error_response_safety.py` (1 test).

### Logs
- Read both files in the codebase that call `logging.*`
  (`app/scheduler.py`, `app/services/backfill.py`) end to end. Neither logs a
  connection string, API key, or token. (`app/services/ai_summary_llm.py`
  already had its own dedicated test from Stage G proving the same for the
  Anthropic key.)

### Hygiene
- `.env.example` gained the 8 settings that existed in `app/config.py` but were
  never documented there: `ADMIN_USER_IDS`, `ALLOWED_USER_IDS`, `OWNER_USER_IDS`,
  `COMPLIANCE_DEFAULT_VALIDITY_DAYS`, `AI_API_KEY`, `AI_MODEL`,
  `AI_SUMMARY_TIMEOUT_SECONDS`, `AI_SUMMARY_DAILY_CAP`.
- Deleted the unused `placeholder()` xfail helper from
  `backend/tests/test_must_pass.py` -- dead code since all 8 must-pass tests
  became real implementations across Stages C-E.

### Full suite
379 passed, 1 skipped (opt-in live RLS check), 0 failed, against a freshly
migrated `db_test` (revision 0006, no new migration this stage) -- includes
the 4 review-finding fixes, the clean-clone-test's `test_config.py`, and the
licence-guard-extension tests from the independent review's finding.

---

## 2. Must-pass tests (CLAUDE.md section 9)

| # | Test | File | Status |
|---|---|---|---|
| 1 | Math/loss calculations correct, incl. a real loss | `test_must_pass.py::test_1` | real, passing |
| 2 | Cash-exceeding buy rejected | `test_must_pass.py::test_2` | real, passing |
| 3 | Non-compliant/expired symbol rejected | `test_must_pass.py::test_3` | real, passing |
| 4 | No future bars (next-bar execution) | `test_must_pass.py::test_4` | real, passing |
| 5 | No fills when market closed/holiday | `test_must_pass.py::test_5` | real, passing |
| 6 | API failure raises, never invents a price | `test_must_pass.py::test_6` | real, passing |
| 7 | Identical run gives identical results | `test_must_pass.py::test_7` | real, passing |
| 8 | Mixed-currency run rejected | `test_must_pass.py::test_8` | real, passing |

Zero `xfail`, zero `skip` on any of the 8. The `placeholder()` helper that used
to generate strict-xfail markers for not-yet-built stages is gone (section 1).

---

## 3. Auth/access table

| Route | Auth | Ownership/Admin/Owner |
|---|---|---|
| `GET /health`, `GET /config` | none (public) | — |
| `GET /me` | `current_user` (allowlist-gated) | — |
| `GET /instruments` | `current_user` | — |
| `GET /instruments/{id}/bars` | `current_user` | 503 if yfinance-sourced and not `is_owner` |
| `GET /watchlist` | `current_user` | — |
| `POST /compliance`, `POST /compliance/csv/*` | `current_user` | `require_admin` |
| `POST /strategy-runs` | `current_user` | rate-limited (10/60s/user) |
| `GET /strategy-runs` | `current_user` | own runs only |
| every `/strategy-runs/{id}/*` (11 routes) | `current_user` | `_get_owned_run` -- 404 for not-found and not-yours alike |
| `POST /strategy-runs/{id}/paper-run` | `current_user` | ownership + rate-limited (10/60s/user) |
| `POST /jobs/backfill` | `X-Job-Secret` header | `hmac.compare_digest` (constant-time) |

Every `current_user`-gated route above is also allowlist-gated (section 1) --
listed once here rather than repeated on every row.

---

## 4. Scans

### gitleaks (full git history + working tree)
- **Git history (all 8 commits): zero leaks.** Command:
  `docker run --rm -v "$(pwd):/repo" zricethezav/gitleaks:latest detect --source=/repo --log-opts="--all"`
- **Working tree (`--no-git`, includes uncommitted files): 21 flagged matches,
  all confirmed non-issues:**
  - 14 in `backend/.venv/Lib/site-packages/...` (cryptography, google/protobuf,
    jwt, numpy, sqlalchemy) -- false positives in third-party dependency source
    (docstring examples, test fixtures), not our code.
  - 2 in `backend/tests/test_access_allowlist.py` / `test_jwt_security.py` --
    an intentionally-fake HS256 test secret (`"test-hs256-secret-..."`), never a
    real credential.
  - 5 pointing at `.env` / `backend/.env` / `frontend/.env.local` -- these
    **are** real local secrets, exactly where they're supposed to be: confirmed
    via `git check-ignore -v` that all three are gitignored
    (`.gitignore`'s `.env` and `.env.*` patterns) and `git status` shows none of
    them tracked. This is expected, not a leak.
  - No raw secret value from this scan was printed to any terminal, log, or this
    document -- only rule id, file path, and line number were ever read back.

### pip-audit
No known vulnerabilities found in any backend dependency.

### npm audit
5 findings (2 moderate, 1 high, 2 critical), all in `vite`/`vitest`/`esbuild`/
`tinypool` -- **dev-tooling only, never shipped in the production frontend
build**. Fix requires a breaking major-version bump of vite/vitest, not done.
**Accepted for now** with this stated reason; the specific esbuild risk ("any
website can send requests to the dev server and read the response") is mitigated
today because `npm run dev` binds to localhost only by default (`package.json`'s
`"dev": "vite"` has no `--host` flag) -- README now states explicitly never to
add one. Schedule the vite/vitest upgrade separately; not a launch blocker on
its own given that mitigation.

---

## 5. Read-only production checks (no secrets printed, scoped SELECTs only)

- **RLS enabled on all 8 tables**: confirmed true for `instruments`,
  `compliance_checks`, `price_bars`, `strategy_runs`, `signals`, `paper_orders`,
  `paper_fills`, `ai_reports`.
- **Zero RLS policies on any of the 8** -- confirmed (the anon key genuinely has
  no access at all; the backend's direct Postgres connection bypasses RLS as the
  table owner, per CLAUDE.md section 6's design).
- **Append-only trigger exists**: `trg_compliance_checks_append_only` on
  `compliance_checks`.
- **`uq_compliance_one_current` index exists** (alongside the primary key and
  `ix_compliance_instrument_checked`).
- **No `DEMO-*` instrument in production** -- confirmed, query returned zero rows.
- **`ai_reports` columns match migration 0006**: `mode`, `requested_mode`,
  `llm_attempts`, `model_version`, `prompt_version`, `input_hash` all present.
- **`strategy_runs` columns match migrations 0004/0005**:
  `blocked_instruments`, `compliance_check_ids`, `paper_status`,
  `paper_completed_at`, `commission_per_order`, `spread_bps`, `slippage_bps` all
  present.
- **`price_bars.source` breakdown in production**: 13,026 rows, **100%**
  `source='yfinance'` (checked previously, unchanged this pass) -- restated here
  because it's the load-bearing fact behind section 6's licensed-provider
  blocker: there is currently no other data in production to fall back to.

---

## 6. Backup & restore plan (documented only -- NOT run against production yet)

### Role: `backup_reader` (read-only, NOT the main `DATABASE_URL` password)

```sql
-- NOT YET EXECUTED. Run once against production when ready to start backups.
CREATE ROLE backup_reader WITH LOGIN PASSWORD '<generate a long random password,
                                                store it in a secrets manager,
                                                never in .env or this file>';
GRANT CONNECT ON DATABASE postgres TO backup_reader;
GRANT USAGE ON SCHEMA public TO backup_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO backup_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO backup_reader;
```

Using a dedicated read-only role (not the backend's own `DATABASE_URL` user)
means a leaked backup credential can read data but can never write, delete, or
alter schema -- a materially smaller blast radius than the app's own password.

### Destination for the encrypted dump

**Not yet decided -- proposed default, flagged as a CEO decision** (same
pattern as CLAUDE.md section 11's open items): a dedicated private object-
storage bucket with a free tier (e.g. Backblaze B2 or Cloudflare R2, ~10GB free),
written to over HTTPS from wherever the backup script runs (initially a
developer's machine or, once it exists, the same GitHub Actions cron proposed
for the daily backfill). The dump is GPG-encrypted locally before it ever leaves
the machine that produced it -- the destination only ever receives ciphertext.
An acceptable interim alternative if no new infrastructure is wanted yet: the
owner's own encrypted external drive or a private personal-cloud folder, as
long as it isn't the same Supabase project it's backing up.

### Backup command (not yet run against production)

```
pg_dump "postgresql://backup_reader:<password>@db.<project_ref>.supabase.co:5432/postgres" \
  --no-owner --no-privileges -Fc -f "backup_$(date +%Y%m%d).dump"
gpg --symmetric --cipher-algo AES256 -o "backup_$(date +%Y%m%d).dump.gpg" "backup_$(date +%Y%m%d).dump"
rm "backup_$(date +%Y%m%d).dump"   # never leave the unencrypted dump on disk
```

### Restore drill (not yet run)

1. `pg_dump` production (via `backup_reader`, as above) to a local `.dump` file.
2. Start the existing `db_test` service (`docker compose --profile test up -d
   db_test`) as the scratch local Postgres -- already the designated throwaway
   database for exactly this kind of destructive operation (CLAUDE.md section 2),
   so no new infrastructure is needed for the drill itself.
3. `pg_restore --no-owner --no-privileges -d <db_test's database> backup.dump`
4. Compare `SELECT count(*)` for all 8 tables between the restored copy and
   production at the time of the dump; expect an exact match (production may
   have moved on since the dump, so re-check close to dump time, not after a
   delay).
5. Stop `db_test` afterward, same as every other stage.

**Supabase free-tier backup/PITR specifics change over time** -- check
Supabase's own current docs at execution time rather than trusting a number
written here; the `pg_dump` path above is the mechanism that works regardless
of what the free tier currently includes.

---

## 7. What blocks a public (non-owner) launch, in priority order

1. ~~Not committed to git.~~ **Resolved 20 Nov 2026**: committed, pushed, repo
   made private. Clean-clone test done (section 8 note below) and independent
   review done (section 0) -- the review's one real finding is already fixed.
2. **Licensed data provider not in place; 100% of production's price data is
   still yfinance.** The `OWNER_USER_IDS` guard (section 1) now covers all three
   known data-exposure surfaces (bars, watchlist, strategy-run/backtest), not
   just the one route it originally covered -- but it still only HIDES the data
   from non-owner users. It does not replace the actual purge once a licensed
   provider is backfilled.
3. **GitHub Actions cron does not exist** (`.github/workflows/` is empty/absent).
   No automated daily backfill, no anti-pause mechanism for the Supabase free
   project, once the backend is deployed anywhere persistent.
4. Backup role + restore drill: documented above, not yet executed against
   production.
5. `npm audit`'s 5 dev-tooling findings: accepted with a stated reason
   (section 4), not blocking, but not forgotten either.

## 8. Clean-clone test (20 Nov 2026)

Fresh `git clone` of the pushed `main` into a scratch directory, following
README only. Found and fixed 3 real deviations:
- `docker compose ... up -d db_test` fails outright if `.env` doesn't exist at
  the repo root, even though `db_test` needs no secret. README now says so.
- Backend needs `.env` in BOTH the repo root and `backend/` -- `app/config.py`
  reads `.env` relative to the current directory, and every backend command
  runs from inside `backend/`. README previously only said "repo root."
- **Real bug, not just a doc gap**: `.env.example` ships `TEST_DATABASE_URL=`
  blank; copying it verbatim sets that env var to `""`, which pydantic-settings
  treats as a real value, silently overriding `config.py`'s non-empty default.
  Every DB-writing test failed with "TEST_DATABASE_URL is not set" on an
  untouched fresh clone. Fixed at the root with a `field_validator` (empty ->
  use the default); 3 tests in `backend/tests/test_config.py`.

Everything else matched README exactly: `pip install -e ".[dev]"`, plain
`pytest` (208 passed, 125 skipped), `RUN_DB_TESTS=1 pytest` (332 passed once
the two `.env` fixes were in place), `npm install`, `npm run build`,
`npm run test`.

**Also confirmed, not a deviation but worth recording**: `alembic`'s CLI always
reads `DATABASE_URL`, never `TEST_DATABASE_URL` -- that env var only matters to
`conftest.py`'s own pytest fixtures. A manual `TEST_DATABASE_URL=... alembic
upgrade head` does nothing to `db_test`; it silently re-targets whatever
`DATABASE_URL` resolves to instead. Harmless here since migrations are
idempotent and `db_test` is migrated correctly by `conftest.py`'s fixture
regardless, but worth knowing before relying on that command for anything.

## CEO decisions still open (CLAUDE.md section 11, restated)
- US-only vs. TSX for the data provider.
- Whether the owner's own pilot counts as personal or commercial data use.
- Backtest-only launch vs. also building live suggestions.
- Stage E's cost-assumption defaults (commission/spread/slippage) --
  placeholders, not researched figures.
- New from this pass: the backup destination (section 6) -- proposed default
  given, not yet decided.
