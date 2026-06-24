"""ResearchAgent 大纲模板匹配示例。

运行：
    python3 examples/research_agent_outline_example.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.research_agent import ResearchAgent


async def main() -> None:
    topic = {
        "workflow_route": "full_rewrite_flow",
        "route_tier": "rewrite_candidate",
        "title": "戴希教授的科研心路：从理论预测到未来科学大奖",
        "primary_keyword": "戴希教授 科研故事",
        "secondary_keywords": ["香港科技大学", "拓扑材料", "未来科学大奖"],
        "target_keywords": ["戴希教授", "科研故事", "未来科学大奖"],
        "content_type": "case_study",
        "content_angle": "general",
        "source_summary": "戴希教授分享科研之路、团队坚持和拓扑材料研究的关键转折。",
        "source_content": (
            "戴希教授将科研比作寻宝，讲述自己和团队多年坚持、经历转折、"
            "最终获得未来科学大奖的故事。他与团队从理论预测出发，推动量子反常霍尔效应"
            "等研究被实验验证，并继续探索拓扑超导材料等未来方向。"
        ),
        "source_url": "https://example.com/story",
        "article_overall_score": 86,
        "article_title_style_score": 82,
        "article_word_count": 900,
    }

    result = await ResearchAgent().execute(topic=topic, mode="mock")
    brief = result["research_brief"]
    outline = brief["writer_outline"]

    print(
        json.dumps(
            {
                "template": {
                    "id": outline.get("template_id"),
                    "name": outline.get("template_name"),
                    "variant_id": outline.get("variant_id"),
                    "variant_name": outline.get("variant_name"),
                },
                "title_instruction": brief.get("title_instruction"),
                "word_count_instruction": brief.get("word_count_instruction"),
                "sections": outline.get("sections"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
