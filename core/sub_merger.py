"""Sub title merger — merge sub trống/dài bất thường trước khi dịch.

Whisper có thể tạo:
- Sub trống dài 30s+ (nhạc nền, im lặng) — sub_splitter không split được
- Sub có text rất ngắn (1-2 chars) lặp lại nhiều lần, kéo dài 70s+ (Whisper lặp)
- Sub có duration bất thường > 30s (audio ồn, Whisper gộp nhiều câu)

Logic:
- Nếu sub text trống + duration > 10s → merge với sub kế tiếp
- Nếu sub text là 1 romaji/kana đơn (1-2 chars JA) + duration > 15s → merge với sub trước
- Nếu sub duration > 30s + text < 5 chars → merge với sub kế (tránh sub rỗng dài)

Sau khi merge, pipeline main chạy sub_splitter bình thường để chia sub > 6s.
"""

from __future__ import annotations

import re
import logging
from typing import List

logger = logging.getLogger(__name__)


# Pattern: romaji/kana ngắn (1-2 chars kana) — Whisper hay để sót
_SHORT_KANA_RE = re.compile(r'^[\u3040-\u309f\u30a0-\u30ff]{1,3}[!?。]?$')


def _parse_time(t: str) -> float:
    """Parse time string 'HH:MM:SS,mmm' hoặc 'HH:MM:SS.mmm' ra giây."""
    t = t.replace(',', '.')
    parts = t.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s


def _format_time(t: float) -> str:
    """Format giây → 'HH:MM:SS,mmm'."""
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')


def _get_duration(start: str, end: str) -> float:
    return _parse_time(end) - _parse_time(start)


def merge_srt_items(items: list, max_empty_dur_s: float = 10.0,
                    short_kana_min_dur_s: float = 15.0,
                    long_empty_min_dur_s: float = 30.0) -> list:
    """Merge sub bất thường trong danh sách items (in-place logic, return new list).

    items: [{'index': 1, 'start': '00:00:01,000', 'end': '00:00:03,000', 'text': '...'}, ...]

    Trả về list mới (không mutate input) đã merge.
    """
    if not items:
        return items

    # Bước 0: gom các sub trùng text liên tiếp (Whisper lặp cùng 1 cụm từ)
    # VD: 塩? / 塩? / 塩? → 1 sub duy nhất
    # Whisper cũng lặp câu DÀI (VD JUL-837: もうやめてくれよ ×6) → gom luôn.
    # Điều kiện: >= 2 sub liên tiếp cùng text VÀ nằm sát nhau trong thời gian
    # (cùng 1 đoạn thoại bị nhân bản), KHÔNG gom nếu cách xa nhau (nhân vật
    # lặp lại câu ở chỗ khác trong phim).
    _DUP_GAP_MAX_S = 2.0
    deduped = []
    i = 0
    while i < len(items):
        cur = items[i]
        cur_text = (cur.get('text') or '').strip()
        # Nếu text ngắn (1-3 chars) và sub tiếp theo có cùng text → gom
        if 1 <= len(cur_text) <= 3 and _SHORT_KANA_RE.match(cur_text):
            j = i + 1
            while (j < len(items)
                   and (items[j].get('text') or '').strip() == cur_text
                   and _get_duration(cur['end'], items[j]['start']) <= _DUP_GAP_MAX_S):
                j += 1
            if j > i + 1:
                # Có >= 2 sub lặp → gom thành 1
                last = items[j - 1]
                merged = {
                    **cur,
                    'end': last['end'],
                    'text': cur_text,
                }
                deduped.append(merged)
                logger.debug(
                    f"SubMerger: dedup {j - i} identical short-kana subs "
                    f"({cur['index']}..{last['index']})"
                )
                i = j
                continue
        # Câu dài bị Whisper nhân bản liên tiếp (>= 2 lần, cách nhau <= 2s)
        elif len(cur_text) >= 4:
            j = i + 1
            while (j < len(items)
                   and (items[j].get('text') or '').strip() == cur_text
                   and _get_duration(cur['end'], items[j]['start']) <= _DUP_GAP_MAX_S):
                j += 1
            if j > i + 1:
                last = items[j - 1]
                deduped.append({**cur, 'end': last['end']})
                logger.info(
                    f"SubMerger: dedup {j - i} identical long subs "
                    f"({cur['index']}..{last['index']}): {cur_text[:30]!r}"
                )
                i = j
                continue
        deduped.append(cur)
        i += 1
    items = deduped

    # Bước 1: gom các sub cần merge (chỉ merge sub rỗng/ngắn với sub kế/trước)
    merged = []
    i = 0
    while i < len(items):
        item = items[i]
        text = (item.get('text') or '').strip()
        dur = _get_duration(item['start'], item['end'])

        # Case 1: sub text trống + duration > 10s → skip, để sub kế merge vào sub trước
        if not text and dur > max_empty_dur_s:
            logger.debug(f"SubMerger: skipping empty sub {item['index']} ({dur:.1f}s)")
            i += 1
            continue

        # Case 1b: Whisper first-token silence split — từ đầu bị gán cả đoạn im lặng 6s-60s
        # rồi bị cắt ngay giữa từ (end của cue này chạm sát start của cue kế tiếp <= 0.25s)
        if text and len(text) <= 5 and dur >= 6.0 and i + 1 < len(items):
            next_item = items[i + 1]
            gap_to_next = abs(_get_duration(item['end'], next_item['start']))
            if gap_to_next <= 0.25:
                next_text = (next_item.get('text') or '').strip()
                joiner = '\n' if text[-1] in '。！？!?…' else ''
                clamped_start = _format_time(
                    max(_parse_time(item['start']), _parse_time(item['end']) - max(0.5, 0.18 * len(text)))
                )
                merged.append({
                    **item,
                    'start': clamped_start,
                    'end': next_item['end'],
                    'text': f"{text}{joiner}{next_text}".strip(),
                })
                logger.debug(
                    f"SubMerger: healed split-word cue {item['index']} ({dur:.1f}s, {text!r}) "
                    f"+ cue {next_item['index']} ({next_text!r})"
                )
                i += 2
                continue

        # Case 1c: CẢ CUE chỉ là mảnh của một từ bị cắt ('あん' | 'たが…',
        # 'いっ' | 'ぱい…', '付き' | '合って…', 'お母' | 'さんの…', 'いい' | 'んじゃない…',
        # 'なん' | 'て言いましたっけ') hoặc chuỗi mảnh vỡ siêu ngắn liên tiếp (< 0.8s, gap <= 0.15s).
        if text and len(text) <= 8 and i + 1 < len(items):
            _tg = _get_merger_tagger()
            nxt_item = items[i + 1]
            nxt_text = (nxt_item.get('text') or '').strip()
            gap_to_next = max(0.0, _get_duration(item['end'], nxt_item['start']))
            if nxt_text and gap_to_next <= 1.5:
                is_whole_split = (
                    len(text) <= 4
                    and (
                        _find_known_split_fragment(text, nxt_text) == text
                        or _find_particle_prefix_fragment(_tg, text, nxt_text) == text
                        or (_tg is not None and _find_fugashi_split_fragment(_tg, text, nxt_text) == text)
                    )
                )
                is_micro_burst = (
                    dur <= 0.80
                    and gap_to_next <= 0.15
                    and text[-1] not in '。！？!?'
                    and _get_duration(item['start'], nxt_item['end']) <= 4.8
                    and len(text) + len(nxt_text) <= 38
                )
                if is_whole_split or is_micro_burst:
                    sep = ' ' if (not is_whole_split and text.endswith(('よ', 'ね', 'でしょ', 'ます', 'です'))) else ''
                    combined_item = {
                        **nxt_item,
                        'start': item['start'],
                        'text': f"{text}{sep}{nxt_text}".strip(),
                    }
                    # Tiếp tục gom nếu các mảnh kế tiếp cũng là micro-burst sát nhau (VD: ADN-780 #152-#161)
                    j = i + 2
                    while j < len(items):
                        cand = items[j]
                        cand_text = (cand.get('text') or '').strip()
                        cand_dur = _get_duration(cand['start'], cand['end'])
                        cand_gap = max(0.0, _get_duration(combined_item['end'], cand['start']))
                        total_dur = _get_duration(combined_item['start'], cand['end'])
                        cur_comb_text = combined_item['text']
                        can_chain_split = (
                            cand_text
                            and cand_gap <= 0.25
                            and total_dur <= 5.0
                            and (
                                _find_known_split_fragment(cur_comb_text, cand_text) is not None
                                or (cand_dur <= 0.80 and cand_gap <= 0.15 and len(cur_comb_text) + len(cand_text) <= 42)
                            )
                        )
                        if not can_chain_split:
                            break
                        c_sep = ' ' if (
                            _find_known_split_fragment(cur_comb_text, cand_text) is None
                            and cur_comb_text.endswith(('よ', 'ね', 'でしょ', 'ます', 'です'))
                        ) else ''
                        combined_item = {
                            **cand,
                            'start': combined_item['start'],
                            'text': f"{cur_comb_text}{c_sep}{cand_text}".strip(),
                        }
                        j += 1
                    merged.append(combined_item)
                    logger.info(
                        f"SubMerger: gop cue manh {item['index']}..{items[j - 1]['index']} "
                        f"-> {combined_item['text'][:32]!r}"
                    )
                    i = j
                    continue

        # Case 2: sub text ngắn (1-3 chars kana) + duration > 15s → merge với sub kế
        if text and _SHORT_KANA_RE.match(text) and dur > short_kana_min_dur_s:
            if i + 1 < len(items):
                next_item = items[i + 1]
                merged.append({
                    **item,
                    'text': (item['text'] + '\n' + next_item['text']).strip(),
                    'end': next_item['end'],
                })
                logger.debug(
                    f"SubMerger: merged sub {item['index']} ({dur:.1f}s, kana) "
                    f"-> sub {next_item['index']}"
                )
                i += 2
                continue

        # Case 3: sub dài > 30s + text ngắn < 5 chars (rất có thể là rỗng/lặp)
        # → merge với sub kế nếu có
        if dur > long_empty_min_dur_s and len(text) < 5:
            if i + 1 < len(items):
                next_item = items[i + 1]
                merged.append({
                    **item,
                    'text': (item['text'] + '\n' + next_item['text']).strip(),
                    'end': next_item['end'],
                })
                logger.debug(
                    f"SubMerger: merged long-empty sub {item['index']} ({dur:.1f}s) "
                    f"-> sub {next_item['index']}"
                )
                i += 2
                continue

        merged.append(item)
        i += 1

    # Bước 2: Khâu các từ tiếng Nhật bị Whisper cắt đôi ở ranh giới 2 câu liên tiếp
    merged = _heal_split_tokens_across_cues(merged)

    # Bước 3: Khâu các vế câu / vị ngữ bị Whisper ngắt đôi (gap <= 0.25s)
    # Ví dụ: '助言を' + 'している', '...から' + 'だと思います', '...内村さんに' + '向けられているのも分かっていた'
    merged = _heal_sentence_predicate_fragments(merged)

    # Re-index
    for new_idx, item in enumerate(merged, 1):
        item['index'] = new_idx

    if len(merged) != len(items):
        logger.info(
            f"SubMerger: {len(items)} -> {len(merged)} subs "
            f"(removed {len(items) - len(merged)} bất thường)"
        )
    return merged


_MERGER_TAGGER = None
_MERGER_TAGGER_FAILED = False

# Loại từ "nội dung" có thể là 1 từ bị Whisper cắt đôi giữa 2 cue
_CONTENT_POS = ('名詞', '形容詞', '形状詞', '副詞', '動詞', '代名詞', '連体詞')
# Như trên + 接続詞 + 感動詞, dùng cho phép thử "token nối vắt qua ranh giới".
_JOINED_CONTENT_POS = _CONTENT_POS + ('接続詞', '感動詞')

# Các từ lóng/Katakana/Hiragana/cụm cố định hay bị Whisper cắt đôi mà UniDic tách thành >1 morpheme
# (khớp theo tiền tố: mảnh cuối cue A + đầu cue B bắt đầu bằng 1 từ trong danh sách)
_KNOWN_SPLIT_WORDS = (
    'おまんこ', 'まんこ', 'おちんちん', 'ちんちん', 'ちんぽ', 'ちんこ',
    'お母さん', 'おかあさん', 'お父さん', 'おとうさん', 'お兄さん', 'お姉さん',
    'すいません', 'すみません', 'ごめんなさい', 'ありがとう', 'よろしく', 'お願いします',
    'そんなこと', 'こんなこと', 'あんなこと', 'どういうこと', 'そういうこと',
    '今まで', 'これまで', 'そのまま', 'このまま', 'そうなんですね', 'そうなんです',
    'やめてって', 'やめて', 'なんで', 'なんとか', 'なんて', 'なんだか',
    'いいんじゃない', 'いいんじゃ', '言ったら', '知ったら', 'ったら', '言ってください', 'ください',
    'チャンス', 'イライラ', 'ドキドキ', 'ムラムラ',
    'ネトラレ', 'オナネタ', 'セフレ', '申し訳', '旦那さん', '奥さん',
    '確実', '媚薬', '美薬', '美役', '気持ち', 'こんなに', 'そんなに', 'あんなに',
    'すごく', 'さらに', 'どっか', 'らしい', 'うん',
    '一日', '一人', '一回', '一度', '一緒',
    '可愛い', '可愛く', 'そしたら', 'それなら', 'だったら',
    'わかんない', 'わかんなく', 'わからない', '濡らして', '濡らし', '濡れて',
)


from .mecab_utils import get_tagger as _get_merger_tagger


def _heal_split_tokens_across_cues(items: list) -> list:
    """Khâu các từ tiếng Nhật bị Whisper cắt đôi ở ranh giới 2 cue liên tiếp.

    Chuyển mảnh bị cắt ở cuối cue A sang đầu cue B (KHÔNG đổi timestamp) khi:
    1. Cuối A + đầu B khớp 1 từ trong `_KNOWN_SPLIT_WORDS` (lấy mảnh dài nhất, vd:
       'おま' + 'んこ', '確' + '実に', 'ち' + 'んぽ', 'イライ' + 'ラ', 'すい' + 'ません').
    2. Cuối A là sokuon đứng lẻ (' っ' / ' ッ') + đầu B ('たら...' -> 'ったら...').
    3. Cuối A và đầu B cùng là Katakana (チャ + ンス -> チャンス).
    4. fugashi: token đầu khi tách lại (mảnh cuối A + cả dòng B) vắt qua ranh giới và là
       từ nội dung, hoặc mảnh cuối A là tiền tố 接頭辞 ('ご', 'お').
    5. Khi CẢ cue A là tiền tố bị cắt nhưng cách cue B > 1.5s (không thể gộp timestamp,
       vd ADN-780 #92 'おか' + #93 'さんのまんこ...' hoặc #70 'そんな' + #71 'こと考えなくて...'),
       khôi phục tiền tố cho đầu cue B mà vẫn giữ cue A hợp lệ.
    """
    if not items or len(items) < 2:
        return items
    tagger = _get_merger_tagger()

    out = [dict(it) for it in items]
    healed_count = 0
    for idx in range(len(out) - 1):
        cur_text = (out[idx].get('text') or '').strip()
        nxt_text = (out[idx + 1].get('text') or '').strip()
        if not cur_text or not nxt_text:
            continue
        if cur_text[-1] in '。！？!?…、,.':
            continue
        if not _JA_CHAR_RE.match(cur_text[-1]) or not _JA_CHAR_RE.match(nxt_text[0]):
            continue

        # Trường hợp đặc biệt: 'おか' (mất 'あ' của おかあさん) + 'さんの...'
        if cur_text == 'おか' and nxt_text.startswith('さんの'):
            out[idx]['text'] = 'お母さん'
            out[idx + 1]['text'] = 'お母' + nxt_text
            healed_count += 1
            continue

        # Trường hợp sokuon ('っ'/'ッ') bị treo ở cuối cue A sau khoảng trắng (ADN-780 #81+#82)
        if re.search(r'[\s　][っッ]$', cur_text) and nxt_text[0] in 'たてとちつkakikukekoかきくけこさしすせそたちつてとぱぴぷぺぽ':
            out[idx]['text'] = cur_text[:-1].rstrip()
            out[idx + 1]['text'] = cur_text[-1] + nxt_text
            healed_count += 1
            continue

        # Trường hợp 'よろしく' ở cuối cue A + 'します...' ở đầu cue B (ADN-780 #67+#68)
        if cur_text.endswith('よろしく') and len(cur_text) > 4 and nxt_text.startswith('します'):
            new_cur = cur_text[:-4].rstrip()
            if new_cur:
                out[idx]['text'] = new_cur
                out[idx + 1]['text'] = 'よろしく' + nxt_text
                healed_count += 1
                continue

        frag = _find_known_split_fragment(cur_text, nxt_text)

        if frag is None:
            frag = _find_particle_prefix_fragment(tagger, cur_text, nxt_text)

        if frag is None and _KATAKANA_RE.match(cur_text[-1]) and _KATAKANA_RE.match(nxt_text[0]) \
                and nxt_text[0] != 'ー':
            m = re.search(r'[\u30A0-\u30FFー]{1,6}$', cur_text)
            if m and len(m.group(0)) < len(cur_text):
                frag = m.group(0)

        if frag is None and tagger is not None:
            frag = _find_fugashi_split_fragment(tagger, cur_text, nxt_text)

        if not frag:
            continue
        new_cur = cur_text[:-len(frag)].rstrip()
        if not new_cur:
            # Nếu cả cue A là tiền tố bị cắt nhưng cách xa cue B (>1.5s nên Case 1c không gộp),
            # khôi phục tiền tố vào đầu cue B để cue B đủ nghĩa mà không làm trống cue A.
            if not nxt_text.startswith(frag):
                out[idx + 1]['text'] = frag + nxt_text
                healed_count += 1
            continue
        out[idx]['text'] = new_cur
        out[idx + 1]['text'] = frag + nxt_text
        healed_count += 1

    if healed_count > 0:
        logger.info(f"SubMerger: healed {healed_count} split Japanese word(s) across adjacent cues")
    return out


_JA_CHAR_RE = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]')
_KATAKANA_RE = re.compile(r'[\u30A0-\u30FF]')
_KANJI_ONLY_RE = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]+$')
# Trợ từ không thể đứng đầu mệnh đề → dấu hiệu câu bị cắt đôi ở ranh giới cue.
_PARTICLE_PREFIXES = ('の', 'を', 'が', 'へ', 'に', 'まで')
_SENTENCE_ENDERS = {
    'ない', 'なく', 'よ', 'ね', 'な', 'わ', 'か', 'の', 'さ', 'ぞ', 'ぜ', 'やん', 'じゃん',
    'だ', 'です', 'ます', 'た', 'て', 'ので', 'から', 'けど', 'もん', 'っけ',
}


def _find_known_split_fragment(cur_text: str, nxt_text: str):
    """Tìm mảnh dài nhất ở cuối cur_text sao cho mảnh + đầu nxt_text = 1 từ đã biết."""
    best = None
    for kw in _KNOWN_SPLIT_WORDS:
        for k in range(len(kw) - 1, 0, -1):
            head, tail = kw[:k], kw[k:]
            if cur_text.endswith(head) and nxt_text.startswith(tail):
                # Chặn ghép nhầm 'う' + 'ん' nếu trước 'う' không phải khoảng trắng (vd: '思う' + 'ん...')
                if kw == 'うん' and len(cur_text) > 1 and not cur_text[:-1].endswith((' ', '　')):
                    continue
                if best is None or len(head) > len(best):
                    best = head
                break
    return best


def _find_particle_prefix_fragment(tagger, cur_text: str, nxt_text: str):
    """Trợ từ ở đầu cue B = dấu hiệu chắc chắn câu bị cắt đôi.

    Trong tiếng Nhật, trợ từ (助詞) の/を/が/へ/に/まで bám vào danh từ phía TRƯỚC nó,
    nên không thể đứng đầu một mệnh đề. Nếu cue B mở đầu bằng trợ từ đó,
    danh từ của nó nằm ở cuối cue A → chuyển sang đầu cue B.
    """
    if tagger is None:
        return None
    try:
        tb_toks = [(t.surface.strip(), getattr(t.feature, 'pos1', '') or '')
                   for t in tagger(nxt_text) if t.surface.strip()]
        ta_toks = [(t.surface.strip(), getattr(t.feature, 'pos1', '') or '')
                   for t in tagger(cur_text) if t.surface.strip()]
    except Exception:
        return None
    if not tb_toks or not ta_toks:
        return None

    surf_b, pos_b = tb_toks[0]
    is_suffix = pos_b == '接尾辞'
    if not is_suffix and not (pos_b == '助詞' and surf_b in _PARTICLE_PREFIXES):
        return None

    surface, pos_a = ta_toks[-1]
    if not surface or len(surface) > 5 or not cur_text.endswith(surface):
        return None
    if pos_a not in _CONTENT_POS:
        return None
    # Hậu tố: kéo theo tiền tố đứng liền trước ('お' + '客' + 'さん', 'お' + '母' + 'さん')
    if is_suffix and len(ta_toks) >= 2 and ta_toks[-2][1] == '接頭辞':
        pre = ta_toks[-2][0]
        if cur_text.endswith(pre + surface):
            surface = pre + surface
    return surface


def _find_fugashi_split_fragment(tagger, cur_text: str, nxt_text: str):
    try:
        toks_a = [t for t in tagger(cur_text) if t.surface.strip()]
        if not toks_a:
            return None
        last = toks_a[-1]
        last_a = last.surface.strip()
        if not last_a or len(last_a) > 4 or not cur_text.endswith(last_a):
            return None
        if len(toks_a) == 1 and len(last_a) > 3:
            return None
        pos1_a = getattr(last.feature, 'pos1', '') or ''
        preceded_by_space = cur_text[:-len(last_a)].endswith((' ', '　'))

        # Trợ từ / trợ động từ dính liền chữ trước = đuôi câu hợp lệ, không phải mảnh bị cắt
        if (pos1_a in ('助詞', '助動詞') or last_a in _SENTENCE_ENDERS) and not preceded_by_space:
            return None
        # Danh từ Hán tự trọn vẹn (>= 2 chữ) không bị cắt
        if len(last_a) >= 2 and _KANJI_ONLY_RE.match(last_a) and not preceded_by_space:
            return None

        if pos1_a == '接頭辞':
            toks_b = [t for t in tagger(nxt_text) if t.surface.strip()]
            if toks_b and (getattr(toks_b[0].feature, 'pos1', '') or '') in ('名詞', '形容詞', '形状詞'):
                return last_a

        joined_toks = [t for t in tagger(last_a + nxt_text) if t.surface.strip()]
        if not joined_toks:
            return None
        t0 = joined_toks[0]
        t0_surf = t0.surface.strip()
        if getattr(t0, 'is_unk', False):
            return None
        if (
            len(t0_surf) > len(last_a)
            and t0_surf.startswith(last_a)
            and (getattr(t0.feature, 'pos1', '') or '') in _JOINED_CONTENT_POS
        ):
            return last_a
    except Exception:
        return None
    return None


# --- Sentence predicate fragment merger ---
_SENTENCE_OPEN_PARTICLES = (
    'を', 'に', 'で', 'と', 'へ', 'より',
    'から', 'ので', 'けど', 'けれど', 'だけ',
    'て', 'ば', 'は', 'も',
)

_SENTENCE_CONTINUATION_PREFIXES = (
    'している', 'してた', 'して', 'した', 'します',
    'です', 'でした', 'ます', 'ました', 'だ', 'だった',
    'だと思います', 'と思います', 'と思う', 'だと思',
    '出ない', '出た', '沈む', '向けられて', '向けて',
    'わけではない', 'わけじゃない',
    'はずです', 'はずだ',
)


def _heal_sentence_predicate_fragments(items: list, max_total_dur: float = 11.5) -> list:
    """Khâu các vế câu / vị ngữ bị Whisper ngắt đôi ở ranh giới 2 cue liên tiếp.

    Khi cue A kết thúc lửng lơ bằng trợ từ mở (を, に, で, と, は, も, から...) và cue B
    bắt đầu ngay lập tức (gap <= 0.25s) với một vị ngữ / trợ động từ ngắn (している, です,
    だと思います, 向けられている...), việc để rời 2 cue khiến LLM dịch lặp lại cả câu
    cho từng mảnh (lỗi DUP_TRANSLATION). Gộp lại thành 1 câu hoàn chỉnh giúp bản dịch
    mượt mà và chính xác.
    """
    if not items or len(items) < 2:
        return items

    merged = []
    i = 0
    while i < len(items):
        cur = items[i]
        if i + 1 >= len(items):
            merged.append(cur)
            i += 1
            continue

        nxt = items[i + 1]
        t1 = (cur.get('text') or '').strip()
        t2 = (nxt.get('text') or '').strip()
        if not t1 or not t2:
            merged.append(cur)
            i += 1
            continue

        dur1 = _get_duration(cur['start'], cur['end'])
        dur2 = _get_duration(nxt['start'], nxt['end'])
        gap = max(0.0, _get_duration(cur['end'], nxt['start']))
        total_dur = _get_duration(cur['start'], nxt['end'])

        can_merge = False
        if gap <= 0.25 and total_dur <= max_total_dur:
            if t1[-1] not in '。！？!?…':
                if not t2.startswith(('はい', 'いいえ', 'うん', 'おう', 'ええ', '何', '誰', 'どう')):
                    ends_open = any(t1.endswith(p) for p in _SENTENCE_OPEN_PARTICLES)
                    starts_cont = any(t2.startswith(p) for p in _SENTENCE_CONTINUATION_PREFIXES)
                    # Điều kiện 1: cue 1 kết thúc bằng trợ từ mở VÀ cue 2 là vị ngữ nối tiếp
                    if ends_open and starts_cont:
                        can_merge = True
                    # Điều kiện 2: cue 2 là vị ngữ ngắn (<= 1.8s hoặc <= 8 ký tự)
                    elif starts_cont and (dur2 <= 1.8 or len(t2) <= 8):
                        can_merge = True
                    # Điều kiện 3: cue 2 là micro-fragment siêu ngắn (<= 0.6s và <= 4 ký tự, vd: 'ですね', 'です')
                    elif ends_open and (dur2 <= 0.6 and len(t2) <= 4):
                        can_merge = True

            # Điều kiện 4: Cue 1 là thán từ / câu hỏi cụt siêu ngắn (<= 1.2s, <= 5 ký tự, vd: 'いいの?', '本当?')
            # và Cue 2 bắt đầu ngay sát (gap <= 0.25s) với một câu mệnh lệnh / hành động (vd: '早く脱げよ それ').
            # Tránh lỗi LLM dịch đón đầu lặp cả 2 dòng (đo ở NSFS-301 cue 115/116).
            if not can_merge and gap <= 0.25 and dur1 <= 1.2 and len(t1) <= 5 and total_dur <= 6.0:
                is_short_interjection = t1.rstrip('?？') in ('いいの', 'いいのか', '本当', 'ほんと', 'どう', 'おい', 'ねえ', 'ほら', 'ちょっと')
                is_action_command = t2.startswith(('早く', '脱げ', '行こ', '入っ', '見せ', '触っ', '止ま', 'やめ', 'して', 'くれ'))
                if is_short_interjection and is_action_command:
                    can_merge = True

        if can_merge:
            merged_item = {
                **nxt,
                'start': cur['start'],
                'text': f"{t1} {t2}".strip(),
            }
            logger.info(
                f"SubMerger: healed sentence fragment {cur['index']}..{nxt['index']} "
                f"({dur1:.1f}s + {dur2:.1f}s = {total_dur:.1f}s): {t1[-15:]!r} + {t2[:15]!r}"
            )
            merged.append(merged_item)
            i += 2
        else:
            merged.append(cur)
            i += 1

    return merged
