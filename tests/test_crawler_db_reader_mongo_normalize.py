import asyncio
import unittest

from agents.crawler_processor_agent.tools.crawler_db_reader import CrawlerDBReader


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_):
        return self

    def limit(self, *_):
        return self

    async def to_list(self, *, length):
        return self._docs[:length]


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs
        self.last_update_filter = None
        self.last_update_doc = None

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._docs)

    async def update_one(self, flt, upd):
        self.last_update_filter = flt
        self.last_update_doc = upd
        return None


class _FakeDB:
    def __init__(self, col):
        self._col = col

    def __getitem__(self, _):
        return self._col


class _FakeClient:
    def __init__(self, db):
        self._db = db

    def __getitem__(self, _):
        return self._db


class TestCrawlerDBReaderMongoNormalize(unittest.TestCase):
    def test_normalize_and_update_doc(self):
        docs = [
            {"_id": "abc", "title": "t", "content": "c", "source_url": "u", "status": "pending"},
        ]
        col = _FakeCollection(docs)
        fake = _FakeClient(_FakeDB(col))

        async def run():
            r = CrawlerDBReader({"type": "mongodb", "database": "d", "collection": "c", "status_field": "status", "pending_status": "pending"})

            async def _fake_get():
                return fake

            r._get_mongo_client = _fake_get  # type: ignore[attr-defined]

            out = await r.read_pending(limit=10)
            self.assertTrue(out.get("success"))
            self.assertEqual(out["data"][0]["id"], "abc")
            self.assertEqual(out["data"][0]["title"], "t")
            await r.update_status(record_id="abc", new_status="processed")
            self.assertIsNotNone(col.last_update_doc)
            self.assertIn("$currentDate", col.last_update_doc)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

