#!/usr/bin/env python3
"""Compare quality scores: original article vs WriterAgent output vs writer_plain.

Usage:
    python3 scripts/run_quality_comparison.py [--host localhost] [--user root] [--password ""]

Outputs summary stats and per-candidate comparison table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare quality scores")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="research_article_data")
    parser.add_argument("--quality-table", default="article_quality_scores")
    parser.add_argument("--candidate-table", default="research_article_candidates")
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    return parser.parse_args()


async def main() -> None:
    import aiomysql

    args = parse_args()
    conn = None
    try:
        conn = await aiomysql.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            db=args.database,
            charset="utf8mb4",
        )

        async with conn.cursor(aiomysql.DictCursor) as cur:
            limit_clause = f"LIMIT {max(1, int(args.limit))}" if int(args.limit) > 0 else ""
            await cur.execute(f"""
                SELECT
                    q_orig.candidate_id,
                    q_orig.quality_score AS original_score,
                    q_writer.quality_score AS writer_score,
                    q_writer.original_quality_score AS writer_orig_ref,
                    q_writer.ai_generated_probability AS writer_ai_prob,
                    q_writer.word_count_score AS writer_word_count,
                    q_writer.fluency_score AS writer_fluency,
                    q_writer.structure_score AS writer_structure,
                    q_writer.attractiveness_score AS writer_attract,
                    q_writer.ai_feel_score AS writer_ai_feel,
                    q_writer.rewrite_feedback_prompt AS writer_feedback,
                    q_plain.quality_score AS plain_score,
                    q_plain.ai_generated_probability AS plain_ai_prob,
                    c.title
                FROM (
                    SELECT candidate_id, quality_score
                    FROM `{args.quality_table}`
                    WHERE source_kind = 'original'
                      AND quality_status = 'scored'
                ) q_orig
                JOIN (
                    SELECT candidate_id, quality_score, original_quality_score,
                           ai_generated_probability, word_count_score,
                           fluency_score, structure_score, attractiveness_score,
                           ai_feel_score, rewrite_feedback_prompt
                    FROM `{args.quality_table}`
                    WHERE source_kind = 'writer'
                      AND quality_status = 'scored'
                ) q_writer ON q_orig.candidate_id = q_writer.candidate_id
                LEFT JOIN (
                    SELECT candidate_id, quality_score, ai_generated_probability
                    FROM `{args.quality_table}`
                    WHERE source_kind = 'writer_plain'
                      AND quality_status = 'scored'
                ) q_plain ON q_orig.candidate_id = q_plain.candidate_id
                LEFT JOIN `{args.candidate_table}` c ON c.id = q_orig.candidate_id
                ORDER BY (q_writer.quality_score - q_orig.quality_score) DESC
                {limit_clause}
            """)
            rows = await cur.fetchall()

        if not rows:
            print("No matching data found.")
            return

        original_scores = [r["original_score"] or 0 for r in rows]
        writer_scores = [r["writer_score"] or 0 for r in rows]
        deltas = [(r["writer_score"] or 0) - (r["original_score"] or 0) for r in rows]
        plain_scores = [r["plain_score"] for r in rows if r["plain_score"] is not None]
        plain_deltas = [r["plain_score"] - r["original_score"] for r in rows if r["plain_score"] is not None]

        def stats(vals):
            vals = [float(v) for v in vals]
            n = len(vals)
            avg = sum(vals) / n if n else 0
            sorted_v = sorted(vals)
            median = sorted_v[n // 2] if n else 0
            return {"n": n, "avg": round(avg, 2), "med": round(median, 2), "min": round(min(vals), 2), "max": round(max(vals), 2)}

        summary = {
            "original_vs_writer": {
                "original": stats(original_scores),
                "writer": stats(writer_scores),
                "delta": stats(deltas),
            },
            "original_vs_writer_plain": {
                "plain": stats(plain_scores) if plain_scores else None,
                "delta": stats(plain_deltas) if plain_deltas else None,
            },
            "ai_gate_impact": {
                "writer_passed_85": sum(1 for s in writer_scores if s >= 85),
                "writer_below_85": sum(1 for s in writer_scores if s < 85),
                "pass_rate_pct": round(sum(1 for s in writer_scores if s >= 85) / len(writer_scores) * 100, 1) if writer_scores else 0,
            },
            "total_candidates": len(rows),
        }

        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print("=" * 90)
            print("QUALITY COMPARISON: Original Article to WriterAgent Output")
            print("=" * 90)
            print(f"\n{'Statistic':<35} {'Original':>10} {'Writer':>10} {'Delta':>10}")
            print("-" * 65)
            for key in ["avg", "med", "min", "max"]:
                orig = summary["original_vs_writer"]["original"][key]
                wri = summary["original_vs_writer"]["writer"][key]
                delta = summary["original_vs_writer"]["delta"][key]
                print(f"{'  ' + key.capitalize():<35} {orig:>10.2f} {wri:>10.2f} {delta:>+10.2f}")
            print(f"\n  Samples: {summary['total_candidates']}")
            print(f"  Passed >=85: {summary['ai_gate_impact']['writer_passed_85']}")
            print(f"  Below 85:   {summary['ai_gate_impact']['writer_below_85']}")
            print(f"  Pass rate:  {summary['ai_gate_impact']['pass_rate_pct']}%")

            if plain_scores:
                print(f"\n{'=' * 90}")
                print("WRITER_PLAIN (if_ai_generated=False) SUBSET")
                print("=" * 90)
                print(f"\n{'Statistic':<35} {'Original':>10} {'Plain':>10} {'Delta':>10} {'Writer':>10}")
                print("-" * 75)
                for key in ["avg", "med", "min", "max"]:
                    orig = summary["original_vs_writer"]["original"][key]
                    pln = summary["original_vs_writer_plain"]["plain"][key] if plain_scores else {}
                    dlt = summary["original_vs_writer_plain"]["delta"][key] if plain_scores else {}
                    wri = summary["original_vs_writer"]["writer"][key]
                    print(f"{'  ' + key.capitalize():<35} {orig:>10.2f} {pln:>10.2f} {dlt:>+10.2f} {wri:>10.2f}")
                print(f"\n  Writer_plain samples: {len(plain_scores)}")

            print(f"\n{'=' * 90}")
            print("PER-CANDIDATE DETAIL (top 15 by improvement)")
            print("=" * 90)
            print(f"{'ID':>6} {'Original':>10} {'Writer':>10} {'Delta':>8} {'Plain':>8} {'AI%':>6} {'Pass':>6}")
            print("-" * 55)
            for row in rows[:15]:
                candidate_id = row["candidate_id"]
                orig = row["original_score"] or 0
                wri = row["writer_score"] or 0
                delta = wri - orig
                plain = row["plain_score"]
                ai = row["writer_ai_prob"]
                passed = "✓" if wri >= 85 else ""
                plain_str = f"{plain:>8.2f}" if plain is not None else " " * 8
                ai_str = f"{ai:>5.0f}" if ai is not None else " " * 5
                print(f"{candidate_id:>6} {orig:>10.2f} {wri:>10.2f} {delta:>+8.2f} {plain_str} {ai_str} {passed:>6}")
            if len(rows) > 15:
                print(f"  ... and {len(rows) - 15} more rows (use --limit to control)")
            print(f"\n  Tip: Run with --json for machine-readable output.")
            print(f"  To score writer_plain: python3 scripts/run_quality_agent_from_db.py --source-kind writer_plain --limit 10")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
