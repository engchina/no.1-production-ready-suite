"""query history の挙動を保護するテスト。"""

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from docrag.knowledge.query_history import (
    append_query_history,
    load_query_history_records,
    load_query_suggestion_blocklist,
    query_history_path,
    query_history_suggestion_choices,
    query_history_suggestion_labels,
    select_query_history_suggestion,
    suggest_query_history_questions,
)
from docrag.config import get_settings


class QueryHistoryTests(unittest.TestCase):
    def test_query_history_is_disabled_by_default(self):
        with TemporaryDirectory() as tmp:
            path = append_query_history(
                output_dir=tmp,
                question="拠点名を変更したい",
                enabled=False,
            )

            self.assertIsNone(path)
            self.assertFalse(query_history_path(tmp).exists())

    def test_appends_and_suggests_popular_queries_with_thresholds(self):
        with TemporaryDirectory() as tmp:
            for run_id in ("run-1", "run-2", "run-3"):
                append_query_history(
                    output_dir=tmp,
                    question="拠点名を変更する方法",
                    enabled=True,
                    run_id=run_id,
                    answer_id=f"answer-{run_id}",
                    retrieval_scope="knowledge_base",
                    classification_filter={"large_category": "10_販売管理"},
                )
            append_query_history(
                output_dir=tmp,
                question="会員証を出力する方法",
                enabled=True,
                run_id="run-4",
                answer_id="answer-4",
                classification_filter={"large_category": "20_在庫管理"},
            )

            records = load_query_history_records(tmp)
            suggestions = suggest_query_history_questions(
                "拠点名 変更",
                records,
                classification_filter={"large_category": "10_販売管理"},
                min_count=2,
                min_unique_runs=2,
                limit=5,
            )

        self.assertEqual([suggestion.question for suggestion in suggestions], ["拠点名を変更する方法"])
        self.assertEqual(query_history_suggestion_choices(suggestions), [["拠点名を変更する方法"]])
        self.assertEqual(query_history_suggestion_labels(suggestions), ["拠点名を変更する方法 / 3件"])
        self.assertEqual(select_query_history_suggestion([["拠点名を変更する方法"]]), "拠点名を変更する方法")

    def test_filters_by_retention_and_blocklist(self):
        with TemporaryDirectory() as tmp:
            history_path = query_history_path(tmp)
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "schema_version": 1,
                                "query_id": "old",
                                "created_at": "2026-01-01T00:00:00Z",
                                "question": "古い質問",
                                "normalized_question": "古い質問",
                                "run_id": "run-old",
                                "classification_filter": {},
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "schema_version": 1,
                                "query_id": "blocked",
                                "created_at": "2026-09-08T00:00:00Z",
                                "question": "秘密を含む質問",
                                "normalized_question": "秘密を含む質問",
                                "run_id": "run-blocked",
                                "classification_filter": {},
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            records = load_query_history_records(
                tmp,
                retention_days=30,
                now=datetime(2026, 9, 8, tzinfo=timezone.utc),
            )
            suggestions = suggest_query_history_questions(
                "",
                records,
                min_count=1,
                min_unique_runs=1,
                blocklist={"秘密"},
            )

        self.assertEqual(suggestions, [])

    def test_loads_naive_iso_timestamps_as_utc_for_retention(self):
        with TemporaryDirectory() as tmp:
            history_path = query_history_path(tmp)
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "query_id": "naive",
                        "created_at": "2026-09-08T00:00:00",
                        "question": "拠点名を変更する方法",
                        "normalized_question": "拠点名を変更する方法",
                        "run_id": "run-naive",
                        "classification_filter": {},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            records = load_query_history_records(
                tmp,
                retention_days=30,
                now=datetime(2026, 9, 8, tzinfo=timezone.utc),
            )

        self.assertEqual([record.query_id for record in records], ["naive"])

    def test_loads_blocklist_from_file_and_env_settings(self):
        with TemporaryDirectory() as tmp:
            blocklist_path = Path(tmp) / "custom-blocklist.txt"
            blocklist_path.write_text("# comment\n秘密\n", encoding="utf-8")

            blocklist = load_query_suggestion_blocklist(
                tmp,
                blocklist_path=blocklist_path,
                extra_values={"private"},
            )

            with patch.dict(
                os.environ,
                {
                    "QUERY_HISTORY_ENABLED": "true",
                    "QUERY_HISTORY_RETENTION_DAYS": "7",
                    "QUERY_HISTORY_MIN_COUNT": "2",
                    "QUERY_HISTORY_MIN_UNIQUE_RUNS": "2",
                    "QUERY_HISTORY_SUGGESTION_LIMIT": "4",
                    "QUERY_HISTORY_BLOCKLIST_PATH": str(blocklist_path),
                },
                clear=True,
            ):
                with patch("docrag.config.load_dotenv"):
                    settings = get_settings(dotenv_path=None)

        self.assertEqual(blocklist, {"秘密", "private"})
        self.assertTrue(settings.query_history_enabled)
        self.assertEqual(settings.query_history_retention_days, 7)
        self.assertEqual(settings.query_history_min_count, 2)
        self.assertEqual(settings.query_history_min_unique_runs, 2)
        self.assertEqual(settings.query_history_suggestion_limit, 4)
        self.assertEqual(settings.query_history_blocklist_path, blocklist_path)


if __name__ == "__main__":
    unittest.main()
