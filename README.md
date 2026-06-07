# iddaa Ingest

Initial milestone:
- fetch football bulletins from iddaa
- save snapshots into SQLite
- derive first-layer features using only stored iddaa bulletin data
- prepare the data for later prediction work

## Run once

```powershell
python -m src.iddaa_ingest.cli fetch-live-football
```

```powershell
python -m src.iddaa_ingest.cli fetch-prematch-football
```

```powershell
python -m src.iddaa_ingest.cli fetch-football --type 0
```

```powershell
python -m src.iddaa_ingest.cli fetch-live-scores
```

```powershell
python -m src.iddaa_ingest.cli generate-labels
```

```powershell
python -m src.iddaa_ingest.cli build-training-dataset
```

## Database

- SQLite path: `data/iddaa.sqlite3`

## Daily automation on Windows

This project includes a helper script for Windows Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_windows_task.ps1
```

Optional custom time:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_windows_task.ps1 -DailyTime "08:30"
```

This now creates two tasks:
- a daily pipeline task for `fetch-prematch-football`, `fetch-live-football`, `fetch-live-scores`, `generate-labels`, and `build-training-dataset`
- a repeated live-score task that runs every `15` minutes by default

You can also run the pipeline manually:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ingest_cycle.ps1 -Mode daily
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ingest_cycle.ps1 -Mode live-scores
```
