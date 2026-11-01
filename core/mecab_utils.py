"""Shared Singleton MeCab (fugashi.Tagger) utility across all core modules.

Tránh khởi tạo 4 instance fugashi.Tagger() riêng lẻ trong RAM ở pipeline,
ja_qa_checker, sub_merger và glossary_miner.
"""
import logging
import threading

logger = logging.getLogger(__name__)

_TAGGER = None
_TAGGER_FAILED = False
_TAGGER_LOCK = threading.Lock()


def get_tagger():
    """Trả về singleton fugashi.Tagger (lazy-initialized, thread-safe), hoặc None nếu không có fugashi."""
    global _TAGGER, _TAGGER_FAILED
    if _TAGGER is not None:
        return _TAGGER
    if _TAGGER_FAILED:
        return None
    with _TAGGER_LOCK:
        if _TAGGER is not None:
            return _TAGGER
        if _TAGGER_FAILED:
            return None
        try:
            import fugashi
            _TAGGER = fugashi.Tagger()
            return _TAGGER
        except Exception as e:
            logger.debug(f"fugashi.Tagger init failed: {e}")
            _TAGGER_FAILED = True
            return None
