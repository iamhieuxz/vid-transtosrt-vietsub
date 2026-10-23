"""Test validator KHÔNG warn nhầm khi 'Vâng' là lời lạm đúng.

Validator dùng chung _VANG_RE với post_processor: chỉ warn khi post-processor
THỰC SỰ sẽ sửa (vâng + counter), không warn khi 'Vâng' đứng riêng / là lời lạm.
"""
import sys, os, io, logging
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.validator import Validator


def _captured(fn, *a):
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    logger = logging.getLogger('core.validator')
    logger.addHandler(h)
    old = logger.level
    logger.setLevel(logging.WARNING)
    try:
        fn(*a)
    finally:
        logger.removeHandler(h)
        logger.setLevel(old)
    return stream.getvalue()


def test_no_false_positive_on_polite_vang():
    """'Vâng' là lời lạm đúng -> KHÔNG được warn."""
    v = Validator()
    for text in ['Vâng', 'Vâng ạ', 'Dạ vâng', 'Vâng, cảm ơn anh']:
        out = _captured(v._check_vietnamese_typos, 'はい', text)
        assert 'typo' not in out, f'FALSE POSITIVE on {text!r}: {out}'


def test_warns_on_real_hallucination():
    """'vâng' + counter = lỗi thật -> phải warn (post-processor sẽ sửa)."""
    v = Validator()
    for text in ['Cả vâng đều sai', 'Khi vâng người kết hôn', 'vâng bên đều vội']:
        out = _captured(v._check_vietnamese_typos, '二人', text)
        assert 'typo' in out, f'MISSED real typo in {text!r}'


def test_romaji_artifact_still_warns():
    """Không regress: romaji leak vẫn phải warn."""
    v = Validator()
    for text in ['đây là hentai', 'cảnh ecchi']:
        out = _captured(v._check_vietnamese_typos, 'あな', text)
        assert 'artifact' in out, f'MISSED artifact in {text!r}: {out}'


if __name__ == '__main__':
    test_no_false_positive_on_polite_vang()
    test_warns_on_real_hallucination()
    test_romaji_artifact_still_warns()
    print('\nAll tests passed!')