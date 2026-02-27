"""LiteLLM cache configuration utilities."""

import os
from typing import Any, Literal


def configure_cache(
    backend: Literal["disk", "r2", "redis", "s3"] = "redis",
    **kwargs: Any,
) -> None:
    """Configure LiteLLM cache.

    Args:
        backend: Cache backend type
            - "redis": Redis cache (default, reads from REDIS_HOST, REDIS_PORT, REDIS_PASSWORD env vars)
            - "disk": Local disk cache
            - "r2": Cloudflare R2 (reads from R2_* env vars)
            - "s3": AWS S3 or compatible
        **kwargs: Additional arguments passed to litellm.Cache()

    Examples:
        # Redis (default, set REDIS_HOST, REDIS_PORT, REDIS_PASSWORD)
        configure_cache("redis")

        # Disk cache
        configure_cache("disk")

        # Cloudflare R2 (set R2_BUCKET_NAME, R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)
        configure_cache("r2")
    """
    import litellm
    from litellm.caching.caching import Cache

    if backend == "disk":
        cache_dir = kwargs.pop("disk_cache_dir", os.path.expanduser("~/.cache/litellm"))
        litellm.cache = Cache(type="disk", disk_cache_dir=cache_dir, **kwargs)  # type: ignore[arg-type]

    elif backend == "r2":
        litellm.cache = Cache(
            type="s3",  # type: ignore[arg-type]
            s3_bucket_name=kwargs.pop("s3_bucket_name", os.environ["R2_BUCKET_NAME"]),
            s3_endpoint_url=kwargs.pop("s3_endpoint_url", os.environ["R2_ENDPOINT_URL"]),
            s3_aws_access_key_id=kwargs.pop("s3_aws_access_key_id", os.environ["R2_ACCESS_KEY_ID"]),
            s3_aws_secret_access_key=kwargs.pop("s3_aws_secret_access_key", os.environ["R2_SECRET_ACCESS_KEY"]),
            s3_region_name=kwargs.pop("s3_region_name", "auto"),
            **kwargs,
        )

    elif backend == "redis":
        from redis.backoff import ExponentialBackoff
        from redis.retry import Retry

        litellm.cache = Cache(
            type="redis",  # type: ignore[arg-type]
            host=kwargs.pop("host", os.environ.get("REDIS_HOST")),
            port=kwargs.pop("port", int(os.environ.get("REDIS_PORT", "6379"))),
            password=kwargs.pop("password", os.environ.get("REDIS_PASSWORD")),
            ssl=kwargs.pop("ssl", True),  # Upstash requires TLS
            ttl=kwargs.pop("ttl", 60 * 60 * 24 * 180),  # Default: 180 days (litellm defaults to 60s)
            retry=kwargs.pop("retry", Retry(ExponentialBackoff(base=0.1), retries=3)),
            retry_on_error=kwargs.pop("retry_on_error", [ConnectionError]),
            **kwargs,
        )

    elif backend == "s3":
        litellm.cache = Cache(type="s3", **kwargs)  # type: ignore[arg-type]

    else:
        raise ValueError(f"Unknown cache backend: {backend}")


def disable_cache() -> None:
    """Disable LiteLLM caching."""
    import litellm

    litellm.cache = None
