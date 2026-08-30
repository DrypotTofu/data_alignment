# `scripts/test/`: test scripts grouped by purpose

This directory is organized by **what the test is for**, not by **how it is run**.
All scripts are run directly from the repo root with the `SurgDataColl` conda environment.

## Layout

- **`benchmark/`** — performance and throughput measurements (frame rates, write I/O, ADS throughput). Run these when you change camera worker code, writer threads, or storage format.
- **`probes/`** — one-off diagnostic probes to answer a specific question (e.g., exposure sweep, camera property readbacks, backend comparison). They usually write a small session-like output under `sessions/`.
- **`verification/`** — regression / smoke tests that must pass before a feature is considered done. Add to these when you change annotations, web API, or data pipeline.

## Running

```powershell
# benchmark example
D:\miniconda3\envs\SurgDataColl\python.exe scripts\test\benchmark\benchmark_twincat.py

# probe example
D:\miniconda3\envs\SurgDataColl\python.exe scripts\test\probes\exposure_sweep.py

# verification example
D:\miniconda3\envs\SurgDataColl\python.exe scripts\test\verification\test_web_review.py
```

Do not add generic top-level test files here. If a test touches multiple areas, put it under the area it is primarily verifying and mention the cross-area impact in its docstring.
