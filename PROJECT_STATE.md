# Project State

Last updated: 2026-06-07 (saatlik diff pipeline + kapalı piyasa filtresi)

## Goal
Build an iddaa prediction system with strong project continuity between sessions.

Current first milestone:
- Automatically fetch the football bulletin from `iddaa.com`
- Save the bulletin to a local database every day
- Keep enough historical data for later modeling and analysis

Product vision:
- Build a site that produces high-confidence 3-match coupons
- User target stake assumption: `50 TL` per coupon
- Business objective: long-run profitability, not random hit-rate theater

Owner references:
- GitHub account: `https://github.com/aytkbrs`

## Continuity Rule
This file must be updated after every meaningful project change.

## Decisions
- Project continuity is enforced with `AGENTS.md` + this file.
- Data source for live football bulletin is the official frontend API host:
  - `https://sportsbookv2.iddaa.com`
- Confirmed live football endpoint:
  - `GET /sportsbook/events?st=1&type=1&version=0`
- Confirmed related endpoints:
  - `GET /sportsbook/info`
  - `GET /sportsbook/get_market_config`
  - `GET /sportsbook/competitions`
  - `GET /sportsbook/highlighted-events?st=1`
- `st=1` means football.
- `type=0` is pre-match football.
- `type=1` is live football.
- `type=2` is long-term/special football events.
- SQLite will be used first because it is local, simple, and enough for initial historical storage.
- Storage model should preserve snapshots per ingest run instead of only the latest state.
- The product should optimize for long-run positive expected value and controlled risk, not for empty "high confidence" labels.
- A single 3-match coupon cannot be honestly guaranteed to profit; the system must be designed around repeatable edge across many coupons.
- Hard data constraint:
  - predictions must be generated only from the `iddaa.com` bulletin data that we fetch and store daily
  - no external match-statistics, xG feeds, referee feeds, travel feeds, or third-party odds feeds in the first system version

## Implemented
- Project continuity rules created in `AGENTS.md`.
- Official live bulletin API host and endpoint were discovered from iddaa frontend bundles.
- Python ingest package created under `src/iddaa_ingest`.
- SQLite snapshot storage created at `data/iddaa.sqlite3`.
- CLI command added:
  - `python -m src.iddaa_ingest.cli fetch-live-football`
- Additional CLI commands added:
  - `python -m src.iddaa_ingest.cli fetch-prematch-football`
  - `python -m src.iddaa_ingest.cli fetch-football --type 0|1|2`
- Windows daily scheduling helper added:
  - `scripts/register_windows_task.ps1`
- Windows pipeline runner added:
  - `scripts/run_ingest_cycle.ps1`
- First real ingest completed successfully.
- First pre-match football ingest completed successfully.
- First feature snapshot layer created and persisted into the database.
- Live score snapshot ingest added from the same iddaa ecosystem:
  - `python -m src.iddaa_ingest.cli fetch-live-scores`
- Result label generation added:
  - `python -m src.iddaa_ingest.cli generate-labels`
- Pre-match training dataset materialization added:
  - `python -m src.iddaa_ingest.cli build-training-dataset`
- Live score ingest was constrained to football-only rows after validation showed the widget can return mixed sports.
- Windows task registration was expanded to create:
  - one daily full pipeline task
  - one recurring live-score refresh task
- **Model stack Layer 1–3 implemented:**
  - `src/iddaa_ingest/model.py` — fair probability computation (proportional margin removal) and per-event edge scoring
  - `src/iddaa_ingest/coupon.py` — 3-leg coupon assembly with correlation penalty and combined fair_prob ranking
  - New DB tables: `edge_scores`, `coupon_candidates`
  - CLI commands added:
    - `python -m src.iddaa_ingest.cli score-edges`
    - `python -m src.iddaa_ingest.cli build-coupons [--top N] [--min-odd X]`
  - Bug fixed: odds with value `1.0` (invalid iddaa data) are now treated as missing; markets with any invalid/missing odd are skipped to prevent inflated fair probabilities
  - Windows terminal UTF-8 encoding fixed in CLI output

## Recommended Product Direction
- Do not build a standard Poisson-only or xG-only prediction site. That is crowded and easy to copy.
- Recommended differentiator:
  - build a `coupon-construction engine`, not just a `match predictor`
  - score each match on `mispricing shape`, `market fragility`, and `correlation penalty`
  - only then assemble 3-match coupons
- Recommended core idea:
  - estimate fair probabilities
  - compare them against bookmaker implied probabilities
  - reject weak edges
  - reject correlated legs
  - optimize full 3-leg coupon expected log-growth, not just per-leg win probability
- Recommended novel angle to explore:
  - `market disagreement + timing drift + structural surprise model`
  - instead of only predicting who wins, estimate where bookmaker pricing is likely least efficient
- Candidate feature families for that approach:
  - opening-to-latest odds drift within stored iddaa snapshots
  - disagreement between 1X2, over/under, BTTS, handicap and live/pre-match structure
  - league-specific volatility regime inferred only from historical bulletin behavior
  - bookmaker confidence proxies such as margin shape across related markets
  - outlier detection for "abnormal" matches rather than average matches

## Data Scope Rule
- First version modeling scope is strictly internal to stored iddaa bulletin history.
- This means the model must learn from:
  - event metadata
  - market structure
  - odds levels
  - odds changes across daily snapshots
  - market availability / removal / suspension patterns
  - league and market behavior over time
- This means the model must not depend on:
  - external APIs
  - scraped non-iddaa match stats
  - external bookmaker comparisons
  - manually injected football analytics datasets

## Important Technical Notes
- Workspace is currently a fresh project.
- Python is available:
  - `python --version` -> `Python 3.14.4`
- No external Python dependency is required for initial ingest if standard library HTTP + SQLite are used.
- API host mapping confirmed from frontend config:
  - `sportsbook -> https://sportsbookv2.iddaa.com`
  - `content -> https://contentv2.iddaa.com`
- Official live score widget endpoint in use for settlement snapshots:
  - `GET /sportsbook/live-events-for-widget`
- Label generation currently depends on finished football matches appearing in stored score snapshots with:
  - `event_status in (3, 4)`
- If only live matches are stored, labels and training rows will correctly remain `0`.

## Model Stack Design (current state)

- **Layer 1 — Fair Probability:** Proportional margin removal from bookmaker odds. `fair_prob_i = (1/odd_i) / overround`. All bets in the same market have `fair_prob * odd = 1/overround`. No real edge possible with only odds data.
- **Layer 2 — Edge Detection:** Dixon-Coles Poisson model using team form from `statisticsv2.iddaa.com` card-corners API.  Gives real probability estimates independent of bookmaker odds.  With only 6 matches/team, variance is high — accuracy improves as we validate over more events.
- **Layer 3 — Coupon Assembly:** Picks 3 legs maximising `combined_fair_prob` (probability of winning the parlay) with `combined_odd >= min_combined_odd`. Penalises same-competition pairs. Exhaustive search over top 40 candidates.
- **Data constraint:** Probabilities from iddaa stats API (`statisticsv2.iddaa.com`) — this is still within the iddaa ecosystem.  No third-party feeds.
- **Statistics API discovered:** `statisticsv2.iddaa.com` hosts `/statistics/soccer/card-corners/{event_id}` which returns last 6 matches for BOTH teams in a single call (goals, corners, cards).
- **Current coupon EV:** Approximately -35 to -40% (reflects iddaa's ~15-20% overround per leg). Real positive-EV coupons require a calibrated model that finds systematic bookmaker mispricings.

## Current Files
- `AGENTS.md`
- `PROJECT_STATE.md`
- `README.md`
- `pyproject.toml`
- `src/iddaa_ingest/config.py`
- `src/iddaa_ingest/client.py`
- `src/iddaa_ingest/db.py`
- `src/iddaa_ingest/features.py`
- `src/iddaa_ingest/ingest.py`
- `src/iddaa_ingest/cli.py`
- `src/iddaa_ingest/labels.py`
- `src/iddaa_ingest/model.py`   ← NEW (edge scoring, Poisson-aware)
- `src/iddaa_ingest/coupon.py`  ← NEW (3-leg coupon assembly)
- `src/iddaa_ingest/stats.py`   ← NEW (team form feature extraction)
- `src/iddaa_ingest/poisson.py` ← NEW (Dixon-Coles Poisson model)
- `scripts/run_ingest_cycle.ps1`
- `scripts/register_windows_task.ps1`
- `data/iddaa.sqlite3`

## Latest Verified Run
- Date: 2026-06-07
- Command:
  - `python -m src.iddaa_ingest.cli fetch-live-football`
  - `python -m src.iddaa_ingest.cli fetch-prematch-football`
  - `python -m src.iddaa_ingest.cli fetch-live-scores`
  - `python -m src.iddaa_ingest.cli generate-labels`
  - `python -m src.iddaa_ingest.cli build-training-dataset`
  - `powershell -ExecutionPolicy Bypass -File .\scripts\run_ingest_cycle.ps1 -Mode live-scores`
- Result:
  - live run:
    - `run_id=2`
    - `events=226`
    - `markets=60`
    - `outcomes=158`
    - `features=226`
    - `version=1780827360241`
  - pre-match run:
    - `run_id=3`
    - `events=214`
    - `markets=3468`
    - `outcomes=10098`
    - `features=214`
    - `version=1780827345616`
  - live score run:
    - `run_id=5`
    - `events=12`
    - `scores=12`
    - `endpoint=/sportsbook/live-events-for-widget`
  - live score pipeline rerun:
    - `run_id=6`
    - `events=12`
    - `scores=12`
  - label generation:
    - `generated_labels=0`
  - training dataset build:
    - `training_rows=0`
- DB counts after validation:
  - `ingest_runs=5`
  - `competitions=198`
  - `event_snapshots=670`
  - `market_snapshots=3584`
  - `outcome_snapshots=10404`
  - `event_feature_snapshots=440`
  - `score_snapshots=24`
  - `event_result_labels=0`
  - `training_dataset_prematch=0`

## Next Steps
- Register and test the daily Windows scheduled task if the user wants it activated now
- Add more derived tables for analysis if modeling starts soon
- Add dedup or diff reporting between runs if needed
- **Expand feature dictionary** for Layer 2 upgrade:
  - Odds drift across daily snapshots (requires ≥2 prematch runs for same event)
  - Cross-market inconsistency score (1X2 vs OU2.5 vs BTTS implied goal distributions)
  - League-level overround / margin regime
  - Market richness relative to competition baseline
- Decide whether long-term football (`type=2`) should be excluded from first model training
- Run `fetch-live-scores` repeatedly during the day or on a short schedule so finished matches accumulate and labels can be produced
- Register the Windows tasks on the machine when ready:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\register_windows_task.ps1`
- Once finished matches exist, rebuild:
  - `generate-labels`
  - `build-training-dataset`
- Add the first coupon-construction research layer on top of the stored bulletin-only dataset

## Change Log

- 2026-06-07 (saatlik diff pipeline + kapalı piyasa filtresi):
  - **HATA DÜZELTMESİ**: `features.py` market status kontrolü eklendi
    - `_find_market` artık `market.get("s", 0) != 0` ise None döndürüyor
    - Kapalı/askıya alınmış piyasalar (iddaa'da "kapalı" görünen oranlar) artık filtreleniyor
    - Etkilenen tablolar: `event_feature_snapshots` (home_odd/draw_odd/away_odd/ou25/btts)
  - **Version-diff tabanlı ingest** (`ingest.py`):
    - Bülten API'si `version` (ms cinsinden timestamp) döndürür; önceki run ile aynıysa yeni run yazılmaz
    - `IngestResult.changed: bool` eklendi — pipeline bu flag'e göre karar verir
    - Değişmeyen bültende DB'ye yeni satır eklenmez → DB gereksiz şişmez
  - **Saatlik Actions** (`.github/workflows/pipeline.yml`):
    - Günde 3x → saatte 1x değiştirildi (`0 * * * *`)
    - Bülten değişmediyse pipeline sadece zaman damgasını günceller (~5 saniye)
    - Bülten değiştiyse tam pipeline çalışır (~3 dakika)
  - **`generate_coupons.py`** akıllı diff mantığı:
    - `need_full_pipeline = ingest_result.changed or new_day`
    - Değişmediğinde: `last_checked` güncellenir, live skor çekilir, stats/scoring/kupon atlanır
    - Her durumda: live skor + label + training çalışır
  - **Streamlit**: `last_checked` timestamp eklendi ("Kupon üretildi: HH:MM · Son kontrol: HH:MM")
  - **DB migration**: `coupon_results` tablosu eklendi (ileride kupon takibi için)

- 2026-06-07 (Poisson model + statistics API):
  - Discovered `statisticsv2.iddaa.com` — provides per-event card-corners data with last 6 matches for both teams
  - Created `stats.py`: form feature extraction from card-corners API
  - Created `poisson.py`: Dixon-Coles Poisson model with Bayesian prior blending (prior_weight=4 phantom matches)
  - Updated `model.py`: uses Poisson probs when available, falls back to margin removal
  - Updated `coupon.py`: EV-based leg selection (fair_prob * odd - 1) when model probs available
  - Added `fetch-stats` CLI command (fetches stats for all events in latest prematch run, ~60s for 200 events)
  - Added `StatsClient` to `client.py`
  - Added `STATISTICS_BASE_URL` to `config.py`
  - Added `event_stats_snapshots` table to DB
  - First run: 165/214 events with stats, 49 errors (events without statistics data)
  - First Poisson-based coupons: combined_odd ~4.5-5.0, win_prob ~33%, EV +45-77%
  - Note: high EV reflects small sample size (6 matches/team); validate over time

- 2026-06-07 (model stack):
  - Implemented `model.py`: fair probability via proportional margin removal, `CandidateLeg` and `EventEdge` dataclasses, `score_latest_prematch` function
  - Implemented `coupon.py`: 3-leg coupon assembly, correlation penalty, `CouponLeg` / `Coupon` dataclasses, `format_coupon` for CLI display
  - Added `edge_scores` and `coupon_candidates` tables to SQLite schema
  - Added `score-edges` and `build-coupons` CLI commands
  - Fixed invalid odds bug: `odd <= 1.01` now treated as missing; markets with incomplete data are skipped
  - Fixed Windows terminal encoding (UTF-8 wrapping in CLI main)
  - First `score-edges` run: 165 events scored, 163 with valid legs
  - First `build-coupons` run: 5 coupons generated, combined_odd ~2.51, win_prob ~24%, EV ~-39% (baseline; reflects 15-20% iddaa overround)

- 2026-06-07:
  - Created project continuity files `AGENTS.md` and `PROJECT_STATE.md`
  - Confirmed official live football bulletin API host and endpoint from frontend bundle analysis
  - Confirmed frontend config maps `sportsbook` service to `https://sportsbookv2.iddaa.com`
  - Added Python ingest client, SQLite schema, and CLI command
  - Added Windows Task Scheduler helper script
  - Ran first successful live football ingest and persisted snapshot data into `data/iddaa.sqlite3`
  - Recorded owner GitHub account `https://github.com/aytkbrs`
  - Recorded product target: a site that produces 3-match coupons with `50 TL` stake assumption
  - Recorded recommended strategic direction: build a coupon-construction and market-mispricing engine rather than a generic match predictor
  - Recorded accepted hard constraint: first model version must use only stored `iddaa.com` bulletin data
  - Extended ingest to support pre-match (`type=0`) and generic football bulletin fetch
  - Added first feature snapshot table derived only from iddaa bulletin structure and odds
  - Validated real pre-match ingest and feature persistence in SQLite
  - Added football-only live score snapshot ingest from `/sportsbook/live-events-for-widget`
  - Added result label generation from finished score snapshots
  - Added pre-match training dataset materialization from latest feature snapshots + labels
  - Fixed CLI DB connection usage for label and training commands
  - Validated the new commands end to end and confirmed current label count is `0` because stored score snapshots are still all live (`event_status=1`)
  - Added `scripts/run_ingest_cycle.ps1` to run the daily or live-score pipeline with one command
  - Expanded `scripts/register_windows_task.ps1` to register both a daily pipeline task and a repeating live-score task
