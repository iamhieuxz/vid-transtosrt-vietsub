# -*- coding: utf-8 -*-
"""Test prompt uncen cho JA literal pass.

Bug gốc: PROMPTS['vi'] trong pipeline có rule uncen, nhưng source JA đi qua
ja_translator nên không dùng PROMPTS['vi'] → rule uncen là code chết.
Với uncen mode + literal_mode (1-pass), _LITERAL_SYSTEM_COMPACT là prompt DUY NHẤT
model thấy, mà nó không có rule uncen → model tự dịch "bạn tình" thay vì
"người tình".
"""
import os
import sys

sys.stdout = (sys.stdout.buffer if hasattr(sys.stdout, 'buffer')
              else sys.stdout).write and sys.stdout
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ja_translator import (
    _get_literal_system_prompt,
    _get_system_prompt,
    _LITERAL_SYSTEM_COMPACT,
    _LITERAL_SYSTEM_COMPACT_UNCEN,
)


def test_uncen_mode_uses_uncen_prompt():
    p = _get_literal_system_prompt('uncen')
    assert p == _LITERAL_SYSTEM_COMPACT_UNCEN, p[:80]


def test_default_mode_uses_base_prompt():
    p = _get_literal_system_prompt('default')
    assert p == _LITERAL_SYSTEM_COMPACT, p[:80]


def test_uncen_prompt_forbids_soft_euphemism():
    p = _get_literal_system_prompt('uncen')
    # phai cam do "bạn tình" (tu lich su) va yeu cau "người tình"
    assert 'bạn tình' in p, 'thieu cam "ban tinh"'
    assert 'người tình' in p, 'thieu "nguoi tinh"'
    # cac cap tinh duc bi cam
    for w in ['dễ chịu', 'thoải mái', 'lên đỉnh', 'phóng thích', 'gần gũi']:
        assert w in p, 'thieu cam %r' % w


def test_uncen_prompt_has_key_terms():
    p = _get_literal_system_prompt('uncen')
    for w in ['気持ちいい', 'セックス', '中出し', '射精', '勃起', '犯す']:
        assert w in p, 'thieu term %r' % w


def test_base_prompt_unchanged():
    """Prompt base (default mode) phai giong het truoc khi sua — khong gay
    regression cho phim thuong/anime."""
    p = _get_literal_system_prompt('default')
    assert '勉強=học' in p
    assert 'セックス' not in p, 'prompt default bi pollute noi dung uncen'


def test_full_system_prompt_uncen_has_addon():
    p = _get_system_prompt('uncen')
    assert 'UNCEN MODE' in p
    assert 'GLOSSARY' in p


if __name__ == '__main__':
    tests = [test_uncen_mode_uses_uncen_prompt,
             test_default_mode_uses_base_prompt,
             test_uncen_prompt_forbids_soft_euphemism,
             test_uncen_prompt_has_key_terms,
             test_base_prompt_unchanged,
             test_full_system_prompt_uncen_has_addon]
    failed = 0
    for fn in tests:
        try:
            fn()
            print('[OK] %s' % fn.__name__)
        except AssertionError as e:
            print('[FAIL] %s: %s' % (fn.__name__, e))
            failed += 1
    print('\n%d failed' % failed)
    sys.exit(1 if failed else 0)
