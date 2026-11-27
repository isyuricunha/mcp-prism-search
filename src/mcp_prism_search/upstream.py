"""Bound direct HTTP provider responses before decoding untrusted content."""
from __future__ import annotations

import httpx

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


async def bounded_request(client, method: str, url: str, **kwargs) -> httpx.Response:
    headers = httpx.Headers(kwargs.pop("headers", None))
    # Request identity encoding to avoid allocating a decompression bomb before
    # checking the actual size. Refuse servers that disregard this preference.
    headers["Accept-Encoding"] = "identity"
    async with client.stream(
        method, url, headers=headers, follow_redirects=False, **kwargs
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").strip().lower() not in {
            "", "identity",
        }:
            raise ValueError("Unsupported upstream content encoding")
        length = response.headers.get("content-length")
        if length is not None and int(length) > MAX_RESPONSE_BYTES:
            raise ValueError("Upstream response exceeds size limit")
        data = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ValueError("Upstream response exceeds size limit")
            data.extend(chunk)
        return httpx.Response(
            response.status_code, headers=response.headers, content=bytes(data),
            request=response.request,
        )
