"""Live reader count: one counter for the whole service."""
import os
import unittest
from unittest.mock import patch

from paperflux.src.services import presence
from paperflux.src.services.presence import KEY, WINDOW_SECONDS, beat, readers_label


class FakeRedis:
    def __init__(self, fail=False):
        self.scores = {}
        self.fail = fail
        self._ops = []

    def pipeline(self):
        if self.fail:
            raise ConnectionError("redis down")
        self._ops = []
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, dict(mapping)))
        return self

    def zremrangebyscore(self, key, low, high):
        self._ops.append(("trim", key, low, high))
        return self

    def zcard(self, key):
        self._ops.append(("card", key))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "zadd":
                self.scores.setdefault(op[1], {}).update(op[2])
            elif op[0] == "trim":
                _, key, low, high = op
                floor = float("-inf") if low == "-inf" else float(low)
                ceiling = float(high)
                self.scores[key] = {
                    member: score for member, score in self.scores.get(key, {}).items()
                    if not (floor <= score <= ceiling)
                }
        return (1, 1, len(self.scores.get(KEY, {})))


class PresenceTest(unittest.TestCase):
    def setUp(self):
        presence._local.clear()
        presence._down_until = 0.0

    def test_label(self):
        self.assertEqual(readers_label(1), "1 reading now")
        self.assertEqual(readers_label(4), "4 reading now")

    def test_heartbeats_share_one_set_and_quiet_sessions_drop_off(self):
        redis = FakeRedis()
        self.assertEqual(beat("a", client=redis, now=1_000), 1)
        self.assertEqual(beat("b", client=redis, now=1_030), 2)
        self.assertEqual(beat("a", client=redis, now=1_030), 2)  # same tab, still one reader
        later = 1_030 + WINDOW_SECONDS + 1
        self.assertEqual(beat("c", client=redis, now=later), 1)
        self.assertEqual(set(redis.scores[KEY]), {"c"})

    def test_a_dead_redis_hides_the_count_instead_of_raising(self):
        self.assertIsNone(beat("a", client=FakeRedis(fail=True), now=1))

    def test_missing_session_is_not_counted(self):
        self.assertIsNone(beat("", client=FakeRedis(), now=1))

    def test_one_process_counts_readers_when_redis_is_unset(self):
        with patch.dict(os.environ, {"REDIS_URL": ""}):
            self.assertEqual(beat("a", now=1_000), 1)
            self.assertEqual(beat("b", now=1_030), 2)
            self.assertEqual(beat("a", now=1_030), 2)
            later = 1_030 + WINDOW_SECONDS + 1
            self.assertEqual(beat("c", now=later), 1)

    def test_a_configured_redis_that_is_down_hides_the_count(self):
        presence._down_until = 10**12
        with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:1/0"}):
            self.assertIsNone(beat("a", now=1))
        self.assertEqual(presence._local, {})


if __name__ == "__main__":
    unittest.main()
