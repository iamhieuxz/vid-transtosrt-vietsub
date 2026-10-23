"""Tests cho _filter_glossary_for_window — matching glossary terms theo window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import TranslationPipeline


def make_pipeline():
    """Pipeline instance chỉ cần đủ field cho _filter_glossary_for_window."""
    p = TranslationPipeline.__new__(TranslationPipeline)
    p.enable_glossary = True
    return p


def test_cjk_term_matches_inside_japanese_text():
    """Term kanji phải match khi nằm giữa text Nhật (không có space)."""
    p = make_pipeline()
    glossary = [{'source_term': '一部', 'target_term': 'mot phan'}]
    result = p._filter_glossary_for_window(glossary, ['その一部指示を'])
    assert len(result) == 1, f'CJK term should match, got {len(result)}'


def test_cjk_term_matches_multiple_occurrences():
    p = make_pipeline()
    glossary = [{'source_term': '主人', 'target_term': 'chong'}]
    result = p._filter_glossary_for_window(
        glossary, ['主人はいません', '主人を裏切って'])
    assert len(result) == 1


def test_latin_term_still_requires_word_boundary():
    """Latin term van phai ton trong boundary (khong regression 'masu' trong 'summarize')."""
    p = make_pipeline()
    glossary = [{'source_term': 'masu', 'target_term': 'x'}]
    assert p._filter_glossary_for_window(glossary, ['summarize here']) == []
    assert p._filter_glossary_for_window(glossary, ['emasculate here']) == []
    assert len(p._filter_glossary_for_window(glossary, ['masu desu'])) == 1


def test_empty_inputs_return_empty():
    p = make_pipeline()
    assert p._filter_glossary_for_window([], ['abc']) == []
    assert len(p._filter_glossary_for_window([{'source_term': 'x'}], [])) == 1


def test_longer_term_wins_over_its_substring():
    """'レノ' là substring của 'レノさん' → chỉ giữ term dài."""
    p = make_pipeline()
    glossary = [
        {'source_term': 'レノさん', 'target_term': 'Reno-san'},
        {'source_term': 'レノ', 'target_term': 'Reno'},
    ]
    result = p._filter_glossary_for_window(glossary, ['レノさんが来た'])
    terms = [t['source_term'] for t in result]
    assert 'レノさん' in terms, terms
    assert 'レノ' not in terms, terms


def test_shorter_term_kept_when_used_alone():
    """Nếu có dòng CHỈ dùng term ngắn thì term ngắn phải được giữ lại."""
    p = make_pipeline()
    glossary = [
        {'source_term': 'レノさん', 'target_term': 'Reno-san'},
        {'source_term': 'レノ', 'target_term': 'Reno'},
    ]
    result = p._filter_glossary_for_window(
        glossary, ['レノさんが来た', 'いやいやまいったなレノ'])
    terms = [t['source_term'] for t in result]
    assert 'レノさん' in terms, terms
    assert 'レノ' in terms, f'dong chi chua "レノ" phai van match: {terms}'


def test_shorter_term_dropped_when_always_covered():
    """Mọi vị trí match của term ngắn đều nằm trong term dài -> bỏ term ngắn."""
    p = make_pipeline()
    glossary = [
        {'source_term': 'レノさん', 'target_term': 'Reno-san'},
        {'source_term': 'レノ', 'target_term': 'Reno'},
    ]
    result = p._filter_glossary_for_window(glossary, ['レノさん', 'またレノさん'])
    terms = [t['source_term'] for t in result]
    assert terms == ['レノさん'], terms


def test_question_suffix_term_only_matches_at_end():
    """'ん?' là hậu tố câu hỏi → chỉ match khi ở cuối câu."""
    p = make_pipeline()
    glossary = [{'source_term': 'ん?', 'target_term': 'Hả?'}]
    assert p._filter_glossary_for_window(glossary, ['いいん?終わり']) == []
    assert len(p._filter_glossary_for_window(glossary, ['どうしたん?'])) == 1
    assert len(p._filter_glossary_for_window(glossary, ['ん?'])) == 1


def test_question_suffix_term_at_end_of_last_line():
    """Term ở cuối dòng cuối cùng của window vẫn phải match."""
    p = make_pipeline()
    glossary = [{'source_term': 'え?', 'target_term': 'Hả?'}]
    assert len(p._filter_glossary_for_window(glossary, ['え?'])) == 1
    assert p._filter_glossary_for_window(glossary, ['え? 終わり']) == []


def test_output_keeps_original_glossary_order():
    """Output phải giữ thứ tự glossary gốc (prompt ổn định)."""
    p = make_pipeline()
    glossary = [
        {'source_term': 'ほら', 'target_term': 'Nhìn này'},
        {'source_term': 'レノさん', 'target_term': 'Reno-san'},
        {'source_term': 'ああ', 'target_term': 'À ừ'},
    ]
    result = p._filter_glossary_for_window(
        glossary, ['ああ', 'レノさん', 'ほら'])
    assert [t['source_term'] for t in result] == ['ほら', 'レノさん', 'ああ']


def test_conjugated_verbs_and_adjectives_match_via_lemma():
    """Động từ / tính từ đã chia (濡れていた, 気持ちよかった, 舐めたい, イっちゃう) phải khớp dạng từ điển."""
    p = make_pipeline()
    glossary = [
        {'source_term': '濡れる', 'target_term': 'ướt'},
        {'source_term': '気持ちいい', 'target_term': 'sướng quá'},
        {'source_term': '舐める', 'target_term': 'liếm'},
        {'source_term': 'イク', 'target_term': 'ra'},
    ]
    res = p._filter_glossary_for_window(
        glossary,
        ['あそこが濡れていた', '気持ちよかった?', 'もっと舐めたい', 'もうイっちゃう'],
    )
    matched = [t['source_term'] for t in res]
    assert matched == ['濡れる', '気持ちいい', '舐める', 'イク'], matched
    # Katakana イク không được khớp nhầm với hiragana/kanji いく / 行った ("đi")
    res_go = p._filter_glossary_for_window(
        [{'source_term': 'イク', 'target_term': 'ra'}],
        ['昨日学校へ行った', '早くいくよ'],
    )
    assert res_go == [], res_go


if __name__ == '__main__':
    tests = [
        test_cjk_term_matches_inside_japanese_text,
        test_cjk_term_matches_multiple_occurrences,
        test_latin_term_still_requires_word_boundary,
        test_empty_inputs_return_empty,
        test_longer_term_wins_over_its_substring,
        test_shorter_term_kept_when_used_alone,
        test_shorter_term_dropped_when_always_covered,
        test_question_suffix_term_only_matches_at_end,
        test_question_suffix_term_at_end_of_last_line,
        test_output_keeps_original_glossary_order,
        test_conjugated_verbs_and_adjectives_match_via_lemma,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f'[OK] {fn.__name__}')
            passed += 1
        except AssertionError as e:
            print(f'[FAIL] {fn.__name__}: {e}')
            failed += 1
        except Exception as e:
            print(f'[ERR]  {fn.__name__}: {type(e).__name__}: {e}')
            failed += 1
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)
