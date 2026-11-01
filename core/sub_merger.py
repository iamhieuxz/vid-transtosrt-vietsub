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
                merged_text = cur_text
                merged = {
                    **cur,
                    'end': last['end'],
                }
                # Nếu text ngắn quá và lặp > 1, giữ nguyên 1 dòng (text lặp không có ý nghĩa)
                # Hoặc giữ text gốc nếu chỉ 1 lần
                merged['text'] = cur_text if j == i + 1 else cur_text
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

        # Case 1c: CA CUE chỉ là mảnh của một từ bị cắt ('あん' | 'たが…',
        # 'いっ' | 'ぱい…', '付き' | '合って…'). Không thể "chuyển mảnh sang cue kế"
        # vì sẽ để lại cue rỗng, nên GỘP cả cue vào cue kế và giữ start của cue đầu.
        if text and len(text) <= 3 and i + 1 < len(items):
            _tg = _get_merger_tagger()
            if _tg is not None:
                nxt_text = (items[i + 1].get('text') or '').strip()
                if nxt_text and _find_fugashi_split_fragment(_tg, text, nxt_text) == text:
                    merged.append({
                        **items[i + 1],
                        'start': item['start'],
                        'text': text + nxt_text,
                    })
                    logger.info(
                        f"SubMerger: gop cue manh {item['index']} ({text!r}) "
                        f"vao cue {items[i + 1]['index']} ({nxt_text[:16]!r})"
                    )
                    i += 2
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
    # (VD: '海田さん 吉' + '田さんに資料を' -> '海田さん' + '吉田さんに資料を',
    #      'こっちもちょ' + 'うど終わりました' -> 'こっちも' + 'ちょうど終わりました',
    #      '吉田さん優' + 'しいから' -> '吉田さん' + '優しいから',
    #      'ダメだしそのイライ' + 'ラを私に向けられるのは' -> 'ダメだしその' + 'イライラを私に向けられるのは')
    merged = _heal_split_tokens_across_cues(merged)

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
# Như trên + 接続詞, chỉ dùng cho phép thử "token nối vắt qua ranh giới".
_JOINED_CONTENT_POS = _CONTENT_POS + ('接続詞',)

# Các từ lóng/Katakana/Hiragana hay bị Whisper cắt đôi mà UniDic tách thành >1 morpheme
# (khớp theo tiền tố: mảnh cuối cue A + đầu cue B bắt đầu bằng 1 từ trong danh sách)
_KNOWN_SPLIT_WORDS = (
    'おまんこ', 'まんこ', 'おちんちん', 'ちんちん', 'ちんぽ', 'ちんこ',
    'チャンス', 'イライラ', 'ドキドキ', 'ムラムラ',
    'ネトラレ', 'オナネタ', 'セフレ', '申し訳', '旦那さん', '奥さん',
    '確実', '媚薬', '美薬', '美役', '気持ち', 'こんなに', 'そんなに', 'あんなに',
    'すごく', 'さらに', 'どっか', 'らしい',
)


from .mecab_utils import get_tagger as _get_merger_tagger


def _heal_split_tokens_across_cues(items: list) -> list:
    """Khâu các từ tiếng Nhật bị Whisper cắt đôi ở ranh giới 2 cue liên tiếp.

    Chuyển mảnh bị cắt ở cuối cue A sang đầu cue B (KHÔNG đổi timestamp) khi:
    1. Cuối A + đầu B khớp 1 từ trong `_KNOWN_SPLIT_WORDS` (lấy mảnh dài nhất, vd:
       'おま' + 'んこ', '確' + '実に', 'ち' + 'んぽ', 'イライ' + 'ラ').
    2. Cuối A và đầu B cùng là Katakana (チャ + ンス -> チャンス).
    3. fugashi: token đầu khi tách lại (mảnh cuối A + cả dòng B) vắt qua ranh giới và là
       từ nội dung, hoặc mảnh cuối A là tiền tố 接頭辞 ('ご', 'お').

    Chặn khâu nhầm (đo ở JUR-412/JUR-630/JUQ-387):
    - Trợ từ/trợ động từ cuối câu (よ, ね, や, だ, た, ない...) dính liền chữ trước →
      KHÔNG chuyển ('…んだよ' + 'もうやん' từng thành 'よもう').
    - Mảnh Latin (dòng 'Cảm ơn' / 'Vâng' Whisper để sót).
    - Danh từ Hán tự trọn vẹn >= 2 chữ ('大切' + '助かりました').

    Phiên bản đo trên JUR-624/JUR-630 (427 cue): khâu được 14 ranh giới, gồm cả
    cue CHỈ là mảnh ('あん'|'たが…' → gộp thành 'あんたが…', vì chuyển riêng mảnh
    sẽ để lại cue rỗng). Phần còn lại là mất mora ĐẦU cue (めんな ← ごめんな) —
    mảnh không nằm ở cue bên cạnh nên không thể khâu, cần sửa phía Whisper.
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
# shortcut: không thêm は/も/で/と — hay mở đầu câu thật ('は?', 'で、', 'と…'); cue Whisper của
# JUR-630 gần như không có dấu câu nên rào chắn "A không kết bằng dấu câu" không đủ chặn.
_PARTICLE_PREFIXES = ('の', 'を', 'が', 'へ', 'に')
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
                if best is None or len(head) > len(best):
                    best = head
                break
    return best


def _find_particle_prefix_fragment(tagger, cur_text: str, nxt_text: str):
    """Trợ từ ở đầu cue B = dấu hiệu chắc chắn câu bị cắt đôi.

    Trong tiếng Nhật, trợ từ (助詞) の/を/が/へ bám vào danh từ phía TRƯỚC nó,
    nên không thể đứng đầu một mệnh đề. Nếu cue B mở đầu bằng trợ từ đó,
    danh từ của nó nằm ở cuối cue A → chuyển sang đầu cue B.

    Fix JUR-412: '... 慰めて 離婚' + 'の手続きって 大変ですか'
                 -> '... 慰めて' + '離婚の手続きって 大変ですか'.
    """
    if tagger is None:
        return None
    try:
        # Đọc (surface, pos1) NGAY: token fugashi là view trên buffer dùng chung, gọi
        # tagger() lần 2 làm hỏng token lần 1 (を -> 記号, 細かい -> 感動詞).
        tb_toks = [(t.surface.strip(), getattr(t.feature, 'pos1', '') or '')
                   for t in tagger(nxt_text) if t.surface.strip()]
        ta_toks = [(t.surface.strip(), getattr(t.feature, 'pos1', '') or '')
                   for t in tagger(cur_text) if t.surface.strip()]
    except Exception:
        return None
    if not tb_toks or not ta_toks:
        return None

    # Token đầu cue B phải là trợ từ hậu vị (の/を/が/へ/に) hoặc hậu tố (さん, らしい) —
    # cả hai đều không thể đứng đầu câu.
    surf_b, pos_b = tb_toks[0]
    is_suffix = pos_b == '接尾辞'
    if not is_suffix and not (pos_b == '助詞' and surf_b in _PARTICLE_PREFIXES):
        return None

    # Danh từ/động từ cần trợ từ nằm ở cuối cue A → lấy làm mảnh chuyển sang.
    surface, pos_a = ta_toks[-1]
    if not surface or len(surface) > 5 or not cur_text.endswith(surface):
        return None
    if pos_a not in _CONTENT_POS:
        return None
    # Hậu tố: kéo theo tiền tố đứng liền trước ('お' + '客' + 'さん', '素' + '晴' + 'らしい')
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
        # Cue chỉ có ĐÚNG 1 token vẫn có thể là mảnh bị cắt: 'あん'+'たが…',
        # 'いっ'+'ぱい…', '付き'+'合って…'. Trước đây `len(toks_a) < 2` chặn hết
        # nhóm này (đo trên JUR-624/JUR-630: 3/5 ca bỏ sót là do guard đó).
        # Giới hạn <=3 ký tự để không "ăn" cả một từ đứng riêng ('そうか'+'まだ').
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
            # 接続詞: 'ま'+'た' -> また, 'じ'+'ゃあ' -> じゃあ (JUR-624 bỏ sót 2 ca này
            # vì 接続詞 không nằm trong _CONTENT_POS).
            and (getattr(t0.feature, 'pos1', '') or '') in _JOINED_CONTENT_POS
        ):
            return last_a
    except Exception:
        return None
    return None
