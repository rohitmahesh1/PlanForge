# server/app/services/http.py
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx


_DEFAULT_TIMEOUT = 15.0  # seconds
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.35  # seconds


_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """
    Return a module-level AsyncClient.
    Reuse a single client for connection pooling and HTTP/2.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "assistant-scheduler/1.0"},
        )
    return _client


async def _retryable_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    """
    Minimal async retry logic for transient 5xx/network errors.
    """
    client = get_client()
    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json,
                data=data,
                files=files,
                timeout=timeout or _DEFAULT_TIMEOUT,
            )
            # Retry on 5xx
            if 500 <= resp.status_code < 600 and attempt < _MAX_RETRIES:
                await asyncio.sleep(_BACKOFF_BASE * attempt)
                continue
            return resp
        except (httpx.TransportError, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt >= _MAX_RETRIES:
                raise
            await asyncio.sleep(_BACKOFF_BASE * attempt)
    # Should not reach here, but just in case:
    if last_exc:
        raise last_exc
    raise RuntimeError("request failed with unknown error")


async def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Perform an HTTP request and parse JSON.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    resp = await _retryable_request(
        method, url, headers=headers, params=params, json=json, data=data, files=files, timeout=timeout
    )
    resp.raise_for_status()
    # Be tolerant of empty bodies
    return resp.json() if resp.content else {}


async def http_text(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    timeout: Optional[float] = None,
) -> str:
    """
    Perform an HTTP request and return text.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    resp = await _retryable_request(
        method, url, headers=headers, params=params, data=data, timeout=timeout
    )
    resp.raise_for_status()
    return resp.text


async def close_client() -> None:
    """Close the shared AsyncClient (useful for graceful shutdowns/tests)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
