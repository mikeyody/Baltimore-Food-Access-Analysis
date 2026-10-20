"""Download every raw dataset into data/raw/.

Each source has its own function so it can be run and debugged on its own:

    python -m src.download                 # download everything (skips files already present)
    python -m src.download --only places   # one source
    python -m src.download --force         # re-download even if present

Every successful download is recorded in data/raw/manifest.json (URL, time,
size, SHA-256) so the data inventory can say exactly what was used and when.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src import config

log = logging.getLogger("download")
MANIFEST_PATH = config.RAW_DIR / "manifest.json"


class DownloadError(RuntimeError):
    """Raised with a human-readable explanation when a source can't be fetched."""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    """Return a requests Session that retries transient failures.

    Retries on connection errors and on 429/5xx responses with exponential
    backoff, so a flaky federal server doesn't kill the whole pipeline.
    """
    retry = Retry(
        total=config.HTTP_MAX_RETRIES,
        backoff_factor=config.HTTP_BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = config.HTTP_USER_AGENT
    return session


def _check_response(resp: requests.Response, source: str) -> None:
    """Turn an HTTP error into a DownloadError that says what to do next."""
    if resp.ok:
        return
    hint = {
        401: "the server wants credentials - check your API key",
        403: "access was refused - the URL may have moved or now requires a key",
        404: "the file was not found - the source has probably moved; check config.py",
    }.get(resp.status_code, "the server returned an error")
    raise DownloadError(
        f"[{source}] HTTP {resp.status_code} from {resp.url}: {hint}.\n"
        f"Response starts with: {resp.text[:300]!r}"
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _record(source: str, url: str, path: Path, extra: dict | None = None) -> None:
    """Add/replace this file's entry in data/raw/manifest.json."""
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
    manifest[source] = {
        "url": url,
        "file": path.relative_to(config.PROJECT_ROOT).as_posix(),
        "downloaded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        **(extra or {}),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def download_file(session: requests.Session, url: str, dest: Path, source: str,
                  params: dict | None = None) -> Path:
    """Stream a URL to disk. Writes to a .part file first so a failed
    download never leaves a half-written file that looks complete."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("[%s] GET %s", source, url)
    try:
        with session.get(url, params=params, stream=True,
                         timeout=config.HTTP_TIMEOUT_SECONDS) as resp:
            _check_response(resp, source)
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    except requests.RequestException as exc:
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"[{source}] network error fetching {url}: {exc}") from exc
    tmp.replace(dest)
    log.info("[%s] saved %s (%.1f MB)", source, dest.name, dest.stat().st_size / 1e6)
    return dest


def _extract_zip(zip_path: Path, out_dir: Path) -> list[str]:
    """Unzip into out_dir and return the member names."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)
        return zf.namelist()


def _skip(path: Path, force: bool, source: str) -> bool:
    if path.exists() and not force:
        log.info("[%s] %s already exists - skipping (use --force to refresh)", source, path.name)
        return True
    return False


# ---------------------------------------------------------------------------
# One function per source
# ---------------------------------------------------------------------------
def download_usda_atlas(session: requests.Session, force: bool = False) -> None:
    """USDA ERS food access data: 2025 SRAM (2020 tracts) and 2019 LRAM (2010 tracts)."""
    for source, url, name in [
        ("usda_sram_2025", config.USDA_SRAM_2025_URL, "sram_2025.zip"),
        ("usda_lram_2019", config.USDA_LRAM_2019_URL, "lram_2019.zip"),
    ]:
        dest = config.RAW_DIR / "usda" / name
        if _skip(dest, force, source):
            continue
        download_file(session, url, dest, source)
        members = _extract_zip(dest, dest.parent / dest.stem)
        if not members:
            raise DownloadError(f"[{source}] zip file is empty: {dest}")
        _record(source, url, dest, {"zip_members": members})


def download_places(session: requests.Session, force: bool = False) -> None:
    """CDC PLACES tract estimates, filtered server-side to Baltimore City and our 4 measures.

    Filtering at the API keeps the download tiny (~800 rows instead of ~3 million).
    """
    source = "cdc_places"
    dest = config.RAW_DIR / "places" / f"places_{config.PLACES_RELEASE}_baltimore.csv"
    if _skip(dest, force, source):
        return
    measures = ",".join(f"'{m}'" for m in config.PLACES_MEASURES)
    params = {
        "$where": f"countyfips = '{config.COUNTY_GEOID}' AND measureid in ({measures})",
        "$limit": 50000,
        "$order": "locationname, measureid",
    }
    download_file(session, config.PLACES_URL, dest, source, params=params)
    with dest.open(newline="", encoding="utf-8") as f:
        n_rows = sum(1 for _ in f) - 1
    if n_rows <= 0:
        raise DownloadError(f"[{source}] query returned no rows - check the dataset ID and filters")
    _record(source, config.PLACES_URL, dest, {"query": params, "rows": n_rows})


def download_acs(session: requests.Session, force: bool = False) -> None:
    """ACS 5-year estimates for every Baltimore City tract via the Census Data API."""
    source = "census_acs"
    dest = config.RAW_DIR / "acs" / f"acs5_{config.ACS_YEAR}_baltimore_tracts.csv"
    if _skip(dest, force, source):
        return
    load_dotenv(config.PROJECT_ROOT / ".env")
    key = os.getenv("CENSUS_API_KEY", "").strip()
    if not key:
        raise DownloadError(
            f"[{source}] CENSUS_API_KEY is not set. The Census Data API now requires a key "
            "for every request. Get a free one at https://api.census.gov/data/key_signup.html, "
            "then copy .env.example to .env and paste it in."
        )
    params = {
        "get": "NAME," + ",".join(config.ACS_VARIABLES),
        "for": "tract:*",
        "in": f"state:{config.STATE_FIPS} county:{config.COUNTY_FIPS}",
        "key": key,
    }
    log.info("[%s] GET %s (tracts in %s)", source, config.ACS_URL, config.COUNTY_GEOID)
    try:
        resp = session.get(config.ACS_URL, params=params, timeout=config.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise DownloadError(f"[{source}] network error: {exc}") from exc
    _check_response(resp, source)
    try:
        rows = resp.json()  # first row is the header
    except ValueError as exc:
        # The API returns an HTML page (not JSON) for an invalid key.
        raise DownloadError(
            f"[{source}] expected JSON but got: {resp.text[:200]!r}. "
            "This usually means the API key is invalid or not yet activated "
            "(click the link in the activation email)."
        ) from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    log.info("[%s] saved %s (%d tracts)", source, dest.name, len(rows) - 1)
    # Never store the key in the manifest.
    _record(source, config.ACS_URL, dest,
            {"year": config.ACS_YEAR, "variables": list(config.ACS_VARIABLES), "rows": len(rows) - 1})


def _arcgis_query_all(session: requests.Session, layer_url: str, where: str,
                      source: str) -> list[dict]:
    """Page through an ArcGIS Feature Service layer and return all attribute rows.

    ArcGIS caps each response (often 1,000-2,000 records), so we request pages
    with resultOffset until the server says there are no more.
    """
    meta = session.get(layer_url, params={"f": "json"}, timeout=config.HTTP_TIMEOUT_SECONDS)
    _check_response(meta, source)
    page_size = meta.json().get("maxRecordCount", 1000)

    rows: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,          # WGS84 lon/lat
            "orderByFields": "ObjectId" if "snap" in layer_url else "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "f": "json",
        }
        resp = session.get(f"{layer_url}/query", params=params,
                           timeout=config.HTTP_TIMEOUT_SECONDS)
        _check_response(resp, source)
        payload = resp.json()
        if "error" in payload:
            raise DownloadError(f"[{source}] ArcGIS error: {payload['error']}")
        for feat in payload.get("features", []):
            row = dict(feat["attributes"])
            geom = feat.get("geometry") or {}
            # Keep the service geometry too; used if lat/lon columns are missing.
            row["geom_x"], row["geom_y"] = geom.get("x"), geom.get("y")
            rows.append(row)
        log.info("[%s] fetched %d records so far", source, len(rows))
        if not payload.get("exceededTransferLimit") and len(payload.get("features", [])) < page_size:
            break
        offset += page_size
    return rows


def _write_rows(rows: list[dict], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(k for r in rows for k in r))
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def download_snap_retailers(session: requests.Session, force: bool = False) -> None:
    """Current SNAP-authorized retailers in Maryland from the USDA ArcGIS service."""
    source = "usda_snap_retailers"
    dest = config.RAW_DIR / "snap" / "snap_retailers_md.csv"
    if _skip(dest, force, source):
        return
    rows = _arcgis_query_all(session, config.SNAP_FEATURE_SERVICE, config.SNAP_WHERE, source)
    if not rows:
        raise DownloadError(f"[{source}] no retailers returned for where={config.SNAP_WHERE!r}")
    _write_rows(rows, dest)
    _record(source, config.SNAP_FEATURE_SERVICE, dest,
            {"where": config.SNAP_WHERE, "rows": len(rows)})


def download_tiger(session: requests.Session, force: bool = False) -> None:
    """TIGER/Line tract shapefile (Maryland), 2020 population centroids, and the
    official 2020<->2010 tract relationship file used for crosswalking."""
    files = [
        ("tiger_tracts", config.TIGER_TRACTS_URL,
         config.RAW_DIR / "tiger" / f"tl_{config.TIGER_YEAR}_{config.STATE_FIPS}_tract.zip"),
        ("census_pop_centroids", config.CENPOP_TRACT_URL,
         config.RAW_DIR / "tiger" / Path(config.CENPOP_TRACT_URL).name),
        ("tract_relationship_2020_2010", config.TRACT_RELATIONSHIP_URL,
         config.RAW_DIR / "crosswalk" / Path(config.TRACT_RELATIONSHIP_URL).name),
    ]
    for source, url, dest in files:
        if _skip(dest, force, source):
            continue
        download_file(session, url, dest, source)
        _record(source, url, dest)


def download_open_baltimore(session: requests.Session, force: bool = False) -> None:
    """Optional: farmers markets and the city's own grocery store list.

    These are supplementary, so a failure here is logged as a warning
    rather than stopping the pipeline.
    """
    for name, layer_url in config.OPEN_BALTIMORE_LAYERS.items():
        source = f"open_baltimore_{name}"
        dest = config.RAW_DIR / "open_baltimore" / f"{name}.csv"
        if _skip(dest, force, source):
            continue
        try:
            rows = _arcgis_query_all(session, layer_url, "1=1", source)
            _write_rows(rows, dest)
            _record(source, layer_url, dest, {"rows": len(rows)})
        except DownloadError as exc:
            log.warning("Optional source failed, continuing without it: %s", exc)


SOURCES = {
    "usda": download_usda_atlas,
    "places": download_places,
    "acs": download_acs,
    "snap": download_snap_retailers,
    "tiger": download_tiger,
    "open_baltimore": download_open_baltimore,
}


def run(only: list[str] | None = None, force: bool = False) -> list[str]:
    """Download the selected sources. Returns the names of sources that failed,
    so one broken source doesn't stop the others from downloading."""
    session = make_session()
    failed = []
    for name, func in SOURCES.items():
        if only and name not in only:
            continue
        try:
            func(session, force=force)
        except DownloadError as exc:
            log.error("%s", exc)
            failed.append(name)
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="+", choices=SOURCES, help="download only these sources")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    failed = run(args.only, args.force)
    if failed:
        raise SystemExit(f"Failed sources: {', '.join(failed)} (see errors above)")
    log.info("All downloads complete.")


if __name__ == "__main__":
    main()
