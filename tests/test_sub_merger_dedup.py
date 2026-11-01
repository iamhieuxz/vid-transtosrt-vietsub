# -*- coding: utf-8 -*-
"""Test SubMerger dedup cho cau dai bi Whisper nhan ban lien tiep."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sub_merger import merge_srt_items


def item(idx, start, end, text):
    return {'index': idx, 'start': start, 'end': end, 'text': text}


def test_long_dup_merged():
    """6 sub giong het 'もうやめてくれよ' -> gom 1."""
    items = [
        item(1, '00:00:00,000', '00:00:01,000', 'abc'),
        item(2, '00:04:47,240', '00:04:48,440', 'もうやめてくれよ'),
        item(3, '00:04:48,440', '00:04:48,500', 'もうやめてくれよ'),
        item(4, '00:04:48,500', '00:04:48,560', 'もうやめてくれよ'),
        item(5, '00:04:48,560', '00:04:48,860', 'もうやめてくれよ'),
        item(6, '00:04:48,860', '00:04:49,380', 'もうやめてくれよ'),
        item(7, '00:04:49,420', '00:04:54,480', 'みなみさん前より柔らかくなったな'),
    ]
    out = merge_srt_items(items)
    texts = [o['text'] for o in out]
    assert texts.count('もうやめてくれよ') == 1, texts
    assert len(out) == 3, len(out)


def test_dup_far_apart_kept():
    """Cung text nhung cach xa nhau -> giu ca 2 (nhan vat lap lai o cho khac)."""
    items = [
        item(1, '00:00:00,000', '00:00:02,000', 'はい'),
        item(2, '00:10:00,000', '00:10:02,000', 'はい'),
    ]
    out = merge_srt_items(items)
    assert len(out) == 2, len(out)


def test_dup_within_2s_merged():
    items = [
        item(1, '00:00:00,000', '00:00:01,000', 'ありがとう'),
        item(2, '00:00:02,000', '00:00:03,000', 'ありがとう'),
    ]
    out = merge_srt_items(items)
    assert len(out) == 1, out
    assert out[0]['end'] == '00:00:03,000', out[0]


def test_single_not_affected():
    items = [item(1, '00:00:00,000', '00:00:02,000', 'こんにちは')]
    out = merge_srt_items(items)
    assert len(out) == 1, out


def test_heal_particle_split_real_cues():
    """Cặp cue thật bị cắt giữa cụm (JUR-708 #81, JUR-630 #24/#105/#18) được khâu lại."""
    cases = [
        ('それが、他', 'に気になる人が出来てしまって。', 'それが、', '他に気になる人が出来てしまって。'),
        ('結構のびりしてますからそれ', 'を聞いて安心しました', '結構のびりしてますから', 'それを聞いて安心しました'),
        ('誓ったんです 私', 'の話をまともに聞こう', '誓ったんです', '私の話をまともに聞こう'),
        ('こちらです あり', 'がとうございます', 'こちらです', 'ありがとうございます'),
    ]
    for a, b, want_a, want_b in cases:
        out = merge_srt_items([item(1, '00:00:00,000', '00:00:02,000', a),
                               item(2, '00:00:02,000', '00:00:04,000', b)])
        assert [o['text'] for o in out] == [want_a, want_b], (a, b, out)


def test_heal_does_not_move_content_word_into_next_cue():
    """B mở đầu bằng từ nội dung (形容詞/名詞) → KHÔNG chuyển 'もし'/'ください' (hồi quy khi token fugashi bị đọc muộn)."""
    for a, b in [('いいですかもし', '細かいところでわからない'), ('聞かないでください', 'さっきから なんだよ')]:
        out = merge_srt_items([item(1, '00:00:00,000', '00:00:02,000', a),
                               item(2, '00:00:02,000', '00:00:04,000', b)])
        assert [o['text'] for o in out] == [a, b], out


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    tests = [test_long_dup_merged, test_dup_far_apart_kept,
             test_dup_within_2s_merged, test_single_not_affected,
             test_heal_particle_split_real_cues,
             test_heal_does_not_move_content_word_into_next_cue]
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
