"""HTTP client for raceresult.com API."""

import logging
import time

import httpx

from piclstats.config import settings
from piclstats.scraper.registry import RESULT_LIST_PATTERNS

logger = logging.getLogger(__name__)

CONFIG_URL = "https://my.raceresult.com/{event_id}/results/config?lang=en"
RESULTS_URL = "https://{server}/{event_id}/results/list"

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "piclstats/0.1 (PAMTB results archiver)"},
        )
    return _client


def _throttle() -> None:
    time.sleep(settings.scrape_delay_seconds)


def fetch_config(event_id: int) -> dict:
    """Fetch event config from raceresult.com. Returns raw JSON dict."""
    url = CONFIG_URL.format(event_id=event_id)
    logger.debug("Fetching config: %s", url)
    resp = _get_client().get(url)
    resp.raise_for_status()
    _throttle()
    return resp.json()


def resolve_list_name(config: dict) -> str:
    """Find the individual results list name from config response.

    The config format varies across years — this handles all known variants.
    """
    candidates: list[str] = []

    # 2025 / 2022 format: Lists[].Name
    if "Lists" in config:
        candidates.extend(item["Name"] for item in config["Lists"] if "Name" in item)

    # Older format: TabConfig.Lists[].Name
    tab_config = config.get("TabConfig", {})
    if "Lists" in tab_config:
        candidates.extend(item["Name"] for item in tab_config["Lists"] if "Name" in item)

    # 2024 format: resultLists[] (flat strings, need prefix)
    if "resultLists" in config:
        candidates.extend(config["resultLists"])

    for pattern in RESULT_LIST_PATTERNS:
        for candidate in candidates:
            if pattern in candidate:
                return candidate

    raise ValueError(f"Could not find individual results list. Available lists: {candidates}")


def fetch_results(server: str, event_id: int, key: str, list_name: str) -> dict:
    """Fetch race results for an event. Returns raw JSON dict."""
    url = RESULTS_URL.format(server=server, event_id=event_id)
    params = {
        "key": key,
        "listname": list_name,
        "contest": "0",
        "r": "all",
        "l": "5",
    }
    logger.debug("Fetching results: %s params=%s", url, params)
    resp = _get_client().get(url, params=params)
    resp.raise_for_status()
    _throttle()
    return resp.json()
