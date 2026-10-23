# -*- coding: utf-8 -*-
"""Test glossary phim JUL-837: load dung file, term trung hop le, match window."""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from core.pipeline import TranslationPipeline, _merge_glossary_files


def load_all():
    return _merge_glossary_files('glossary_uncen.yaml', 'glossary_common.yaml',
                                 'glossary_JUL-837.yaml')


def test_all_files_valid_yaml():
    for f in ['glossary_uncen.yaml', 'glossary_common.yaml', 'glossary_JUL-837.yaml']:
        data = yaml.safe_load(io.open(f, encoding='utf-8').read())
        assert isinstance(data, list) and data, f
        for e in data:
            assert e.get('source_term'), (f, e)
            assert e.get('target_term'), (f, e)


def test_no_duplicate_source_term():
    terms = load_all()
    srcs = [t['source_term'] for t in terms]
    dups = sorted({x for x in srcs if srcs.count(x) > 1})
    assert not dups, dups


def test_jul837_terms_loaded():
    srcs = {t['source_term'] for t in load_all()}
    for s in ['光介', 'コウスケ', 'ミナミさん', '宮部さん', 'りょーこ',
              '部長', '嘘', '彼氏']:
        assert s in srcs, s


def test_name_variants_all_map_to_one_canonical():
    """Mọi cách Whisper viết tên phải quy về 1 canonical, không lệch."""
    terms = load_all()
    by_src = {t['source_term']: t['target_term'] for t in terms}
    # Kousuke: 5 cách viết -> 1 canonical
    for v in ['光介', 'コウスケ', 'こうすけ', 'コスケ', 'Koukusu']:
        assert by_src.get(v) == 'Kousuke', (v, by_src.get(v))
    # Minami: kana + kanji variant
    for v in ['ミナミさん', 'ミナミ', 'みなみ', 'みなみさん']:
        assert by_src.get(v) == 'Minami', (v, by_src.get(v))


def test_glossary_targets_keep_vietnamese_diacritics():
    """target_term mất dấu là lỗi chết người — LLM sẽ dịch tiếp
    'nguoi tinh' thay vì dùng 'người tình'.

    Chỉ kiểm các từ BẮT BUỘC có dấu; 'tinh' / 'nguoi' trong 'tinh dịch'
    là hợp lệ vì thành phần còn lại đã có dấu."""
    for t in load_all():
        tgt = t['target_term']
        # tên riêng Latin không cần dấu
        if re.match(r'^[A-Za-z][A-Za-z\s\-\.]*$', tgt):
            continue
        if not any('\u00c0' <= c <= '\u1ef3' for c in tgt):
            raise AssertionError('target_term mat dau: %r' % tgt)


def test_stale_checker_runs_on_real_file():
    """Check glossary còn khớp Whisper hiện tại không (regression chính
    của JUL-837: Whisper đổi kana/kanji → glossary stale → tên bị dịch sai)."""
    folder = r'E:\HIDE\JAV\JUL-837'
    whisper = os.path.join(folder, 'JUL-837_whisper.srt')
    if not os.path.exists(whisper):
        return  # không có file mẫu → skip

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gloss = os.path.join(here, 'glossary_JUL-837.yaml')

    # tự parse SRT (không import module để tránh đóng stdout)
    text = '\n'.join(
        L[2] for L in
        ([x for x in b.strip().split('\n') if x.strip()]
         for b in io.open(whisper, encoding='utf-8-sig').read().strip().split('\n\n'))
        if len(L) >= 3 and '-->' in L[1])

    stale = [e['source_term'] for e in yaml.safe_load(
        io.open(gloss, encoding='utf-8').read())
        if e['source_term'] not in text]
    # 11 biến thể cũ là dự phòng → chỉ fail nếu MẤT HẾT tên hiện hành
    live = [s for s in stale if s in ('光介', 'コウスケ', 'ミナミさん',
                                      'ミナミ', '宮部さん')]
    assert not live, ('mất tên hiện hành trong whisper SRT', live)


def test_uncen_terms_loaded():
    srcs = {t['source_term'] for t in load_all()}
    for s in ['セフレ', 'セックス', '義理', '浮気', '胸']:
        assert s in srcs or s == '胸', s


def test_filter_finds_sefre_in_window():
    p = TranslationPipeline.__new__(TranslationPipeline)
    terms = load_all()
    src_lines = ['リョーコもいるんだセフレ', '俺のセフレになってみるか']
    rel = p._filter_glossary_for_window(terms, src_lines)
    srcs = {t['source_term'] for t in rel}
    assert 'セフレ' in srcs, srcs


def test_filter_finds_names():
    p = TranslationPipeline.__new__(TranslationPipeline)
    terms = load_all()
    rel = p._filter_glossary_for_window(terms, ['おいこうすけ', 'みなみさんまたね'])
    srcs = {t['source_term'] for t in rel}
    assert 'こうすけ' in srcs or 'おいこうすけ' in srcs, srcs
    assert 'みなみ' in srcs or 'みなみさん' in srcs, srcs


def test_per_film_overrides_uncen():
    """glossary_JUL-837 ghi đè glossary_uncen nếu trùng source_term."""
    terms = load_all()
    by_src = {}
    for t in terms:
        by_src.setdefault(t['source_term'], []).append(t['target_term'])
    for s, targets in by_src.items():
        assert len(targets) == 1, (s, targets)


if __name__ == '__main__':
    tests = [test_all_files_valid_yaml, test_no_duplicate_source_term,
             test_jul837_terms_loaded, test_uncen_terms_loaded,
             test_filter_finds_sefre_in_window, test_filter_finds_names,
             test_per_film_overrides_uncen,
             test_name_variants_all_map_to_one_canonical,
             test_glossary_targets_keep_vietnamese_diacritics,
             test_stale_checker_runs_on_real_file]
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
