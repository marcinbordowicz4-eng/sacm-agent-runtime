import os

import redis


class RedisClient:
    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("REDIS_URL") or "redis://localhost:6379"
        self._client = redis.Redis.from_url(self.url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self._client.ping())
