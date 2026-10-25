"""Polite HTTP: at most ~1 request/second and automatic waiting on
'429 Too Many Requests'. Installed once in run.py; it also covers the
euroleague_api package, which calls requests.get internally."""
import time

import requests

_original_get = requests.get
_last_call = [0.0]
MIN_GAP_SECONDS = 1.0
MAX_RETRIES = 6


def polite_get(url, *args, **kwargs):
    r = None
    for attempt in range(MAX_RETRIES):
        wait = MIN_GAP_SECONDS - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        r = _original_get(url, *args, **kwargs)
        _last_call[0] = time.time()
        if r.status_code != 429:
            return r
        retry_after = r.headers.get("Retry-After", "")
        delay = int(retry_after) if retry_after.isdigit() else 30 * (attempt + 1)
        print(f"429 Too Many Requests -> waiting {delay}s ({url})", flush=True)
        time.sleep(delay)
    return r


def install():
    requests.get = polite_get
