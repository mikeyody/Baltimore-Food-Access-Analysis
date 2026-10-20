# Baltimore City Food Access and Health Gap Analysis

Which Baltimore City census tracts have poor access to healthy food, how does that
relate to health outcomes, income and car ownership, and where would new food
resources help the most people?

> Work in progress. The full README (findings, methodology, architecture) is written in Phase 6.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On macOS/Linux, activate with `source .venv/bin/activate` instead.

### Census API key (required)

The Census Data API requires a free key for every request.

1. Request one at https://api.census.gov/data/key_signup.html and click the activation link in the email.
2. Copy `.env.example` to `.env` and paste the key after `CENSUS_API_KEY=`.

`.env` is gitignored, so the key is never committed.

## Run

```powershell
python run_pipeline.py              # every step
python run_pipeline.py download     # just the downloads
python -m src.download --only acs   # a single source
```

Raw files go to `data/raw/` (gitignored). See [reports/data_sources.md](reports/data_sources.md)
for the data inventory.
