import json
import unittest

from scripts import redis_pipeline


class FakeRedis:
    def __init__(self, *, pending=None, claimed=None):
        self.added = []
        self.acked = []
        self.pending = pending if pending is not None else {"pending": 0}
        self.claimed = claimed if claimed is not None else ["0-0", []]
        self.autoclaim_calls = []

    async def xadd(self, stream, fields):
        self.added.append((stream, fields))
        return f"{len(self.added)}-0"

    async def xack(self, stream, group, msg_id):
        self.acked.append((stream, group, msg_id))
        return 1

    async def xpending(self, stream, group):
        return self.pending

    async def xautoclaim(self, stream, group, consumer, min_idle_time, count):
        self.autoclaim_calls.append(
            {
                "stream": stream,
                "group": group,
                "consumer": consumer,
                "min_idle_time": min_idle_time,
                "count": count,
            }
        )
        return self.claimed


class TestRedisPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_handle_failure_retries_before_deadletter(self):
        fake = FakeRedis()
        original = {"article_id": 42, "title": "hello"}

        await redis_pipeline.handle_failure(
            fake,
            stream=redis_pipeline.STREAM_SCORING,
            group=redis_pipeline.GROUP_SCORING,
            msg_id="1-0",
            item=original,
            stage="scoring",
            error="temporary failure",
            max_retries=2,
        )

        self.assertEqual(fake.acked, [(redis_pipeline.STREAM_SCORING, redis_pipeline.GROUP_SCORING, "1-0")])
        self.assertEqual(fake.added[0][0], redis_pipeline.STREAM_SCORING)
        payload = json.loads(fake.added[0][1]["data"])
        self.assertEqual(payload["article_id"], 42)
        self.assertEqual(payload["retry_count"], 1)
        self.assertEqual(payload["failed_stage"], "scoring")
        self.assertEqual(payload["last_error"], "temporary failure")
        self.assertNotIn("retry_count", original)

    async def test_handle_failure_moves_to_deadletter_after_retry_limit(self):
        fake = FakeRedis()

        await redis_pipeline.handle_failure(
            fake,
            stream=redis_pipeline.STREAM_REWRITE,
            group=redis_pipeline.GROUP_REWRITE,
            msg_id="2-0",
            item={"article_id": 7, "retry_count": 2},
            stage="rewrite",
            error="permanent failure",
            max_retries=2,
        )

        self.assertEqual(fake.added[0][0], redis_pipeline.STREAM_DEADLETTER)
        fields = fake.added[0][1]
        self.assertEqual(fields["stage"], "rewrite")
        self.assertEqual(fields["source_stream"], redis_pipeline.STREAM_REWRITE)
        self.assertEqual(fields["source_msg_id"], "2-0")
        payload = json.loads(fields["data"])
        self.assertEqual(payload["article_id"], 7)
        self.assertEqual(payload["retry_count"], 3)
        self.assertEqual(fake.acked, [(redis_pipeline.STREAM_REWRITE, redis_pipeline.GROUP_REWRITE, "2-0")])

    async def test_recover_pending_claims_idle_messages(self):
        fake = FakeRedis(
            pending={"pending": 2},
            claimed=["0-0", [("1-0", {"data": "{}"}), ("2-0", {"data": "{}"})]],
        )

        result = await redis_pipeline.recover_pending(
            fake,
            redis_pipeline.STREAM_PUBLISH,
            redis_pipeline.GROUP_PUBLISH,
            "consumer-1",
        )

        self.assertEqual(result, fake.claimed)
        self.assertEqual(len(fake.autoclaim_calls), 1)
        self.assertEqual(fake.autoclaim_calls[0]["stream"], redis_pipeline.STREAM_PUBLISH)
        self.assertEqual(fake.autoclaim_calls[0]["group"], redis_pipeline.GROUP_PUBLISH)
        self.assertEqual(fake.autoclaim_calls[0]["consumer"], "consumer-1")

    async def test_recover_pending_noops_when_empty(self):
        fake = FakeRedis(pending={"pending": 0})

        result = await redis_pipeline.recover_pending(
            fake,
            redis_pipeline.STREAM_QUALITY,
            redis_pipeline.GROUP_QUALITY,
            "consumer-2",
        )

        self.assertIsNone(result)
        self.assertEqual(fake.autoclaim_calls, [])

    def test_response_count_helpers_accept_redis_py_variants(self):
        self.assertEqual(redis_pipeline._pending_message_count({"pending": 3}), 3)
        self.assertEqual(redis_pipeline._pending_message_count([4, None, None, None]), 4)
        self.assertEqual(redis_pipeline._claimed_message_count({"messages": [1, 2]}), 2)
        self.assertEqual(redis_pipeline._claimed_message_count(["0-0", [("1-0", {})]]), 1)
        self.assertEqual(redis_pipeline._stream_message_count([("pipeline:scoring", [])]), 0)
        self.assertEqual(redis_pipeline._stream_message_count([("pipeline:scoring", [("1-0", {})])]), 1)
