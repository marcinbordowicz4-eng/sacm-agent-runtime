import json
import os
from typing import Any

import redis


class RedisClient:
    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("REDIS_URL") or "redis://localhost:6379"
        self._client = redis.Redis.from_url(self.url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self._client.ping())

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        self._client.setex(key, ttl_seconds, json.dumps(value, sort_keys=True))

    def publish_json(self, channel: str, value: dict[str, Any]) -> None:
        self._client.publish(channel, json.dumps(value, sort_keys=True))
