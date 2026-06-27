import asyncio

from agents.scoring_agent import TopicAgent


def test_topic_agent_generates_scored_topic_list():
    agent = TopicAgent()

    result = asyncio.run(
        agent.execute(
            keywords=["EMBA", "商学院"],
            industry="商学院/高管教育",
            target_audience="企业高管、创业者",
            output_count=5,
        )
    )

    topics = result["topics"]
    assert len(topics) == 5
    assert topics[0]["score"]["total_score"] >= topics[-1]["score"]["total_score"]
    assert topics[0]["title"]
    assert topics[0]["target_keywords"]
    assert topics[0]["score"]["dimension_scores"]["search_value"] >= 0


def test_topic_agent_scores_manual_topic_with_custom_weights():
    agent = TopicAgent()

    score = agent.score_topic(
        {
            "title": "EMBA报考指南",
            "primary_keyword": "EMBA报考指南",
            "search_volume": 800,
            "keyword_difficulty": 20,
        },
        scoring_criteria={
            "search_value": {"weight": 0.5},
            "competition_feasibility": {"weight": 0.3},
            "intent_match": {"weight": 0.2},
            "content_uniqueness": {"weight": 0},
            "strategic_value": {"weight": 0},
        },
    )

    assert score.total_score > 0
    assert score.priority in {"high", "medium", "low"}
    assert score.recommendation in {"采纳", "修改后采纳", "暂缓"}
