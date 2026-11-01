"""Tests cho nhánh lỗi của TranslationPipeline._process_single_task (không cần LLM/DB thật)."""
import os
import sys
import threading
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import TranslationPipeline


def make_pipeline(window_result, valid=True):
    p = TranslationPipeline.__new__(TranslationPipeline)
    p.db = MagicMock()
    p.translator = MagicMock()
    p.validator = MagicMock()
    p.validator.validate_window_content.return_value = valid
    p._context_lock = threading.Lock()
    p._get_context = MagicMock(return_value=[])
    p._translate_window = MagicMock(return_value=window_result)
    p._save_to_tm = MagicMock()
    p._retry_per_line = MagicMock(return_value=None)
    p._get_glossary_for_window = MagicMock(return_value=[])
    return p


def make_task(retry_count=0):
    return {
        'id': 7, 'window_index': 3, 'start_sub_index': 1, 'end_sub_index': 2,
        'start_pos': 0, 'end_pos': 1, 'retry_count': retry_count,
        'original_text': '[1] こんにちは\n[2] さようなら',
    }


PROJECT = {'source_lang': 'ja', 'target_lang': 'vi', 'name': 'T'}


def test_llm_none_marks_failed_instead_of_committing_japanese():
    p = make_pipeline(None)
    assert p._process_single_task(1, make_task(), PROJECT, True) is False
    p.db.commit_window.assert_not_called()
    p.db.mark_task_failed.assert_called_once()


def test_validation_fail_first_time_retries_not_commits():
    p = make_pipeline((['a', 'b'], ['こんにちは', 'さようなら']), valid=False)
    assert p._process_single_task(1, make_task(retry_count=0), PROJECT, True) is False
    p.db.commit_window.assert_not_called()
    p.db.mark_task_failed.assert_called_once()


def test_validation_fail_second_time_commits_as_is():
    p = make_pipeline((['a', 'b'], ['こんにちは', 'さようなら']), valid=False)
    assert p._process_single_task(1, make_task(retry_count=1), PROJECT, True) is True
    p.db.commit_window.assert_called_once()
    p._save_to_tm.assert_not_called()  # không làm bẩn TM bằng window fail validate
