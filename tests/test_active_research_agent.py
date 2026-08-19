import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agents.active_research_agent.active_research_agent import ActiveResearchAgent, _strip_source_suffix


class ActiveResearchAgentTests(unittest.IsolatedAsyncioTestCase):
    def test_keyword_groups_have_no_weight_side_effect(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))
        pairs = agent.iter_keywords({"keyword_groups": {"mba": ["MBA"], "edu": ["考研"]}})

        self.assertEqual(pairs, [("mba", "MBA"), ("edu", "考研")])

    def test_score_candidate_uses_transparent_breakdown(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"), min_topic_score=75)
        published = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
        now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)

        score, breakdown, reasons = agent._score_candidate(
            title="MBA招生政策发布，管理类联考报名条件调整",
            summary="多所商学院同步更新申请流程，方便考生准备材料。",
            keyword="MBA",
            published_at=published,
            source_name="中国教育在线",
            now=now,
        )

        self.assertGreaterEqual(score, 75)
        self.assertIn("business_relevance", breakdown)
        self.assertIn("keyword_scope_match", reasons)

    def test_source_suffix_is_removed_from_google_news_title(self):
        self.assertEqual(
            _strip_source_suffix("上海交大成立AI时代商科人才培养联盟 - news.sjtu.edu.cn", "news.sjtu.edu.cn"),
            "上海交大成立AI时代商科人才培养联盟",
        )

    def test_rss_parser_keeps_source_url_for_traceability(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))
        raw = """
        <rss><channel><item>
          <title>标题 - example.com</title>
          <link>https://news.google.com/rss/articles/abc</link>
          <pubDate>Tue, 18 Aug 2026 21:32:09 GMT</pubDate>
          <source url="https://example.com">example.com</source>
        </item></channel></rss>
        """

        entries = agent._parse_rss_entries(raw)

        self.assertEqual(entries[0]["source"], "example.com")
        self.assertEqual(entries[0]["source_url"], "https://example.com")

    def test_dedupe_keeps_highest_scored_candidate_for_same_title(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))
        base = {
            "title": "MBA招生政策发布",
            "url": "https://example.com/a?utm_source=x",
            "source_name": "source",
            "source_url": "https://example.com",
            "source_type": "rss",
            "original_url": "https://example.com/a",
            "original_url_status": "direct",
            "keyword": "MBA",
            "keyword_group": "mba",
            "published_at": None,
            "summary": "",
            "fetched_at": "2026-08-18T00:00:00+00:00",
            "dedup_key": "a",
            "score_breakdown": {},
            "reasons": [],
        }
        low = agent.to_jsonable([])
        self.assertEqual(low, [])

        from agents.active_research_agent.active_research_agent import ActiveResearchCandidate

        first = ActiveResearchCandidate(topic_score=70, **base)
        second = ActiveResearchCandidate(topic_score=90, **{**base, "url": "https://example.com/b", "dedup_key": "b"})

        out = agent._dedupe_candidates([first, second])

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].topic_score, 90)

    async def test_google_news_url_requires_secondary_search_before_research(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))

        original_url, status, warnings = await agent._resolve_original_url(
            client,
            discovery_url="https://news.google.com/rss/articles/abc?oc=5",
            source_url="https://example.com",
        )
        await client.aclose()

        self.assertEqual(original_url, "")
        self.assertEqual(status, "needs_secondary_search")
        self.assertIn("original_url_requires_secondary_search", warnings)

    async def test_google_news_url_decodes_to_original_article(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith("https://news.google.com/articles/abc"):
                return httpx.Response(200, text='<c-wiz><div jscontroller data-n-a-sg="sig" data-n-a-ts="123"></div></c-wiz>')
            if str(request.url).startswith("https://news.google.com/_/DotsSplashUi/data/batchexecute"):
                return httpx.Response(200, text=')]}\'\n\n[[null,null,"[null, \\"https://example.com/article?utm_source=x\\"]"]]')
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        original_url, status, warnings = await agent._resolve_original_url(
            client,
            discovery_url="https://news.google.com/rss/articles/abc?oc=5",
            source_url="https://example.com",
        )
        await client.aclose()

        self.assertEqual(original_url, "https://example.com/article")
        self.assertEqual(status, "decoded_google_news")
        self.assertEqual(warnings, [])

    def test_research_brief_blocks_unresolved_google_news_original(self):
        agent = ActiveResearchAgent(keyword_config_path=Path("unused.yml"))

        from agents.active_research_agent.active_research_agent import ActiveResearchCandidate

        candidate = ActiveResearchCandidate(
            title="MBA热点",
            url="https://news.google.com/rss/articles/abc",
            source_name="example.com",
            source_url="https://example.com",
            source_type="google_news_rss",
            original_url="",
            original_url_status="needs_secondary_search",
            keyword="MBA",
            keyword_group="mba",
            published_at=None,
            summary="摘要",
            fetched_at="2026-08-18T00:00:00+00:00",
            dedup_key="abc",
            topic_score=80,
            score_breakdown={},
            reasons=[],
            warnings=["original_url_requires_secondary_search"],
        )

        brief = agent.build_research_brief(candidate)

        self.assertFalse(brief["research_ready"])
        self.assertEqual(brief["stop_reason"], "original_url_unresolved")
        self.assertEqual(brief["discovery_url"], candidate.url)


if __name__ == "__main__":
    unittest.main()
