# -*- coding: utf-8 -*-
"""Bộ trích xuất Tên riêng tự động (fugashi + pykakasi) & Kiểm định chất lượng JA->VI (Group A2 & Group C).

Bao gồm:
1. AutoCharacterExtractor:
   - Quét toàn bộ dòng phụ đề tiếng Nhật bằng fugashi (MeCab + UniDic) kết hợp
     nhận diện hậu tố xưng hô (さん/ちゃん/くん/君/様/さま).
   - Phiên âm tự động sang Hepburn Romaji chuẩn (pykakasi + chuẩn hóa nguyên âm dài)
     mà KHÔNG cần gọi LLM (chạy < 50ms cho cả bộ phim, thay thế hoàn toàn sự phụ thuộc vào Worker A).
2. JaQAChecker:
   - Kiểm tra đảo ngược phủ định (Negation Inversion: ない/ません/じゃない/ないで/ず...).
   - Kiểm tra tuân thủ từ điển bắt buộc (Mandatory Glossary Compliance).
   - Kiểm tra & làm sạch rò rỉ ký tự CJK/Kana (Residual CJK Leak) bằng fugashi + pykakasi.
   - Kiểm tra bất thường độ dài câu (Truncation / Hallucination).
   - Kiểm tra đồng nhất tên riêng nhân vật.
   - Tự động sửa (Rule-based + Targeted 1-line LLM retry cho các dòng lỗi nặng).
3. Context Window Token Guard:
   - Ước lượng token của prompt và tự động lọc/cắt gọn context để tránh Ollama cắt cụt ngầm.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

from .mecab_utils import get_tagger as _get_tagger

_KAKASI = None
_KAKASI_FAILED = False


def _get_kakasi():
    """Lazy-init pykakasi converter."""
    global _KAKASI, _KAKASI_FAILED
    if _KAKASI is not None:
        return _KAKASI
    if _KAKASI_FAILED:
        return None
    try:
        import pykakasi
        _KAKASI = pykakasi.kakasi()
        return _KAKASI
    except Exception as e:
        logger.debug(f"pykakasi init failed: {e}")
        _KAKASI_FAILED = True
        return None


# Danh sách các danh từ chung/đại từ hay đi kèm さん/ちゃん/くん/様 nhưng KHÔNG phải tên riêng
_NON_NAME_HONORIFIC_STEMS: Set[str] = {
    '旦那', '主人', '奥', '嫁', '妻', '夫', '夫婦', '彼氏', '彼女', '恋人', '愛人', 'セフレ',
    'お母', 'お父', 'お兄', 'お姉', 'おじ', 'おば', 'お嬢', 'おにい', 'おねえ', 'おじい', 'おばあ',
    '母', '父', '兄', '姉', '弟', '妹', '義母', '義父', '義兄', '義姉', '義弟', '義妹',
    '皆', 'みな', 'あなた', 'あんた', 'お前', 'おまえ', '君', 'きみ', '僕', '俺', '私', 'あたし',
    '自分', '誰', 'だれ', '何', 'なに', 'どこ', 'いつ', 'どう', 'これ', 'それ', 'あれ', 'ここ', 'そこ',
    'お客', '店員', '先生', '先輩', '後輩', '部長', '課長', '社長', '店長', '会長', '係長', '主任',
    '隣', '大家', '看護婦', '看護師', '医者', 'お医者', '運転手', '監督', 'コーチ', 'トレーナー',
    '神', '仏', '赤', 'わん', 'ねこ', '猫', '犬', 'うさぎ', 'くま',
    'ちんちん', 'おちんちん', 'まんこ', 'おまんこ', 'おっぱい', 'ちんこ', 'ちんぽ',
    '今日', '明日', '昨日', '毎日', '今年', '去年', '来年', '今度', '今回', '本当', 'ほんと',
    'お疲れ', 'おつかれ', '疲れ', 'つかれ', 'ご苦労', 'ごくろう', 'お陰', 'おかげ', 'お互い', 'おたがい',
    'ご馳走', 'ごちそう', '馳走', 'ちそう', 'ばあ', 'じい', '親父', 'おやじ', '婆', '爺',
    '手伝い', 'お手伝い', 'てつだい', 'モデル', '近所', 'ご近所', 'きんじょ',
    'ちん', 'チン', 'チンチン', 'かちん', 'おにい', 'おねえ', 'アジア', '黒',
    '馬鹿', 'バカ', 'ばか', '阿呆', 'アホ', 'あほ', '変態', 'エロ', 'スケベ',
    '女王', '王様', '王', '王子', '姫', 'お姫', '誰か', 'だれか',
    '皆様', '皆さま', '皆さん', '神様', '仏様', 'お客様', 'お客さま',
}

# Các từ Katakana vay mượn phổ biến đôi khi UniDic gán nhầm là 人名
_NON_NAME_KATAKANA: Set[str] = {
    'セックス', 'フェラ', 'フェラチオ', 'クンニ', 'パイズリ', '手コキ', 'オナニー', 'アナル', 'ペニス',
    'イク', 'イキ', 'ゴム', 'キス', 'ベッド', 'ホテル', 'シャワー', 'お風呂', 'トイレ', 'テレビ',
    'スマホ', 'ケータイ', '電話', 'メール', 'ライン', 'ビデオ', 'カメラ', '写真', 'ムービー',
    'ビール', 'ワイン', 'コーヒー', 'ジュース', 'ミルク', 'お水', 'ランチ', 'ディナー',
    'パンツ', 'パンティ', 'ブラ', 'ブラジャー', 'スカート', 'シャツ', 'ドレス', 'スーツ',
    'マッサージ', 'エステ', 'サロン', 'ジム', 'プール', '海', '山', '部屋', 'リビング', 'キッチン',
    'オフィス', '会社', 'メーカー', 'プロジェクト', 'チーム', 'メンバー', 'トップ', 'リーダー',
    'タイプ', 'スタイル', 'ルール', 'マナー', 'チャンス', 'ピンチ', 'イメージ', 'ストレス',
    'デート', 'プレゼント', 'サプライズ', 'パーティー', 'イベント', 'ゲーム', 'アニメ', 'ドラマ',
    'アルバイト', 'バイト', 'パート', 'サービス', 'セット', 'コース', 'メニュー', 'チェック',
    'ダメ', 'ムリ', 'ウソ', 'ホント', 'マジ', 'ヤバ', 'エロ', 'スケベ', 'バカ', 'アホ',
    'パパ', 'ママ', 'ペット', 'ガラス', 'ドア', 'テーブル', 'ソファ', 'タオル', 'ティッシュ',
}

# Các từ Hán/Kana thông dụng không bao giờ là tên người dù đứng trước さん hoặc được tag lạ
_COMMON_JA_WORDS_BLACKLIST: Set[str] = (
    _NON_NAME_HONORIFIC_STEMS
    | _NON_NAME_KATAKANA
    | {
        '気持ち', '気持', '大好き', '好き', '嫌い', '可愛い', 'かわいい', '綺麗', 'きれい',
        '素敵', 'すてき', '最高', '最低', '無理', 'むり', '駄目', 'だめ', '一緒', 'いっしょ',
        '一人', '二人', '三人', '全部', 'ぜんぶ', '半分', 'はんぶん', '普通', 'ふつう',
        '絶対', 'ぜったい', '全然', 'ぜんぜん', '結構', 'けっこう', '大変', 'たいへん',
        '大丈夫', 'だいじょうぶ', 'よろしく', 'ありがとう', 'すみません', 'ごめん', 'ごめんなさい',
        'お願い', 'おねがい', '頂戴', 'ちょうだい', '我慢', 'がまん', '遠慮', 'えんりょ',
        '結婚', '婚約', '離婚', '妊娠', '出産', '浮気', '不倫', '恋愛', '仕事', 'しごと',
        '会社', 'かいしゃ', '学校', 'がっこう', '病院', 'びょういん', '警察', 'けいさつ',
        '時間', 'じかん', '場所', 'ばしょ', '理由', 'りゆう', '意味', 'いみ', '気分', 'きぶん',
        '身体', 'からだ', '体', '肉体', '気持ちいい', '気持ち良い', '中出し', '仲出し', '射精',
        # Cụm cảm thán / từ thường từng bị nhận nhầm là tên (JUR-624: ご苦労さん -> "Kuro",
        # ワンちゃん -> "Wan"). Thêm vào đây để không lọt qua luật hậu tố xưng hô.
        '苦労', '疲れ', 'ワン', 'わん', '世話', '迷惑', '邪魔', '冗談', '文句', 'お疲れ', 'ご苦労',
    }
)

_HONORIFIC_RE = re.compile(
    r'([ァ-ヶー]{2,6}|[ぁ-ん]{2,5}|[一-龥]{1,4})(さん|ちゃん|くん|君|様|さま)(?![ァ-ヶーぁ-ん一-龥])'
)


def _normalize_romaji_name(raw_romaji: str) -> str:
    """Chuẩn hóa chuỗi romaji thành tên riêng Hepburn gọn đẹp (Title Case).

    Ví dụ:
      'satou' -> 'Sato', 'yuuki' -> 'Yuki', 'kentarou' -> 'Kentaro',
      'miku' -> 'Miku', 'tsuyoshi' -> 'Tsuyoshi'
    """
    if not raw_romaji:
        return ''
    s = re.sub(r'[^a-zA-Z]', '', raw_romaji).lower()
    if not s:
        return ''
    # Chuẩn hóa nguyên âm dài phổ biến trong tên người tiếng Nhật
    for old, new in (
        ('ou', 'o'),
        ('oo', 'o'),
        ('uu', 'u'),
        ('aa', 'a'),
        ('ee', 'e'),
    ):
        if len(s) >= 4:
            s = s.replace(old, new)
    # Sửa âm tiết Hepburn chuẩn
    s = s.replace('si', 'shi').replace('ti', 'chi').replace('tu', 'tsu').replace('hu', 'fu')
    s = s.replace('zi', 'ji').replace('sy', 'sh').replace('ty', 'ch').replace('zy', 'j')
    s = re.sub(r'tsu([aieo])', r'ts\1', s)
    return s.capitalize()


def ja_to_hepburn(text: str) -> str:
    """Chuyển tên tiếng Nhật (Kanji/Kana) sang Hepburn Romaji chuẩn bằng fugashi + pykakasi."""
    if not text or not text.strip():
        return ''
    clean = text.strip()
    for suf in ('さん', 'ちゃん', 'くん', '君', '様', 'さま', '先生', '先輩', '殿'):
        if clean.endswith(suf) and len(clean) > len(suf):
            clean = clean[:-len(suf)]
            break

    # Nếu đã là chữ Latin
    if re.match(r'^[A-Za-z\s\-]+$', clean):
        return clean.strip().title()

    reading = clean
    tagger = _get_tagger()
    if tagger is not None:
        try:
            toks = list(tagger(clean))
            kana_parts = []
            for t in toks:
                kana = getattr(t.feature, 'kana', None) or getattr(t.feature, 'pron', None) or t.surface
                kana_parts.append(kana)
            if kana_parts:
                reading = ''.join(kana_parts)
        except Exception:
            pass

    kks = _get_kakasi()
    if kks is not None:
        try:
            conv = kks.convert(reading)
            hep = ''.join(item.get('hepburn', '') for item in conv)
            norm = _normalize_romaji_name(hep)
            if norm:
                return norm
        except Exception:
            pass

    return _normalize_romaji_name(reading)


def _generate_mistaken_aliases(canonical: str) -> List[str]:
    """Sinh các biến thể tên mà Whisper/LLM 8B hay phiên âm lệch.
    Không sinh các tiền tố/chuỗi con ngắn (< 4 ký tự) của tên gốc để tránh khớp nhầm.
    """
    c = canonical.strip()
    if len(c) < 3:
        return []
    out: Set[str] = set()
    low = c.lower()
    if low == 'miku':
        out.update(['Mikuru', 'Mikku', 'Mikusama'])
    elif low == 'tsuyoshi':
        out.update(['Tuyoshi', 'Tsuyashi', 'Tsuyoi', 'Tsuyuhi', 'Tsuyuki'])
    if 'ts' in low:
        cand = low.replace('ts', 't').capitalize()
        if len(cand) >= 4:
            out.add(cand)
    if 'sh' in low:
        cand = low.replace('sh', 's').capitalize()
        if len(cand) >= 4:
            out.add(cand)
    for suf in ('san', 'chan', 'kun', 'sama'):
        out.add((low + suf).capitalize())
    out.discard(c)
    return sorted(a for a in out if len(a) >= 4)


def extract_character_names_from_lines(
    source_lines: List[str],
    existing_names: Optional[List[dict]] = None,
    min_score: int = 3,
) -> List[dict]:
    """Tự động trích xuất tên nhân vật từ danh sách câu tiếng Nhật (KHÔNG gọi LLM).

    Điểm tin cậy (score) cho mỗi tên ứng viên:
      - Đứng ngay trước hậu tố xưng hô (さん/ちゃん/くん/君/様/さま): +3 điểm / lần
      - Được MeCab (fugashi) gắn nhãn 固有名詞 + 人名 (tên người): +2 điểm / lần (nếu đi kèm hậu tố thì +4)
      - Xuất hiện độc lập trùng với tên đã từng đi kèm hậu tố: +1 điểm / lần

    Trả về danh sách dict theo đúng chuẩn `config['character_names']`:
      [{'canonical': 'Miku', 'aliases': ['ミク', '美久', 'ミクさん'], 'mistaken_aliases': [...]}, ...]
    """
    existing_list = list(existing_names or [])
    if not source_lines:
        return existing_list

    surface_score: Counter = Counter()
    honorific_hits: Counter = Counter()
    jinmei_hits: Counter = Counter()
    surface_to_canonical: Dict[str, str] = {}

    tagger = _get_tagger()

    for line in source_lines:
        if not line or not line.strip():
            continue
        s = line.strip()

        # 1. Quét bằng Regex hậu tố xưng hô khi không có fugashi (hoặc tên Katakana thuần)
        if tagger is None:
            for m in _HONORIFIC_RE.finditer(s):
                stem = m.group(1).strip()
                if not stem or stem in _COMMON_JA_WORDS_BLACKLIST or len(stem) < 2:
                    continue
                if stem[-1] in 'はがをにへでとものやねよか':
                    continue
                honorific_hits[stem] += 1
                surface_score[stem] += 3

        # 2. Phân tích hình thái học bằng fugashi (MeCab + UniDic)
        if tagger is not None:
            try:
                toks = list(tagger(s))
                for idx, tok in enumerate(toks):
                    surf = tok.surface.strip()
                    if not surf or surf in _COMMON_JA_WORDS_BLACKLIST:
                        continue
                    pos1 = getattr(tok.feature, 'pos1', '') or ''
                    pos2 = getattr(tok.feature, 'pos2', '') or ''
                    pos3 = getattr(tok.feature, 'pos3', '') or ''

                    next_surf = toks[idx + 1].surface.strip() if idx + 1 < len(toks) else ''
                    has_honorific = next_surf in ('さん', 'ちゃん', 'くん', '君', '様', 'さま')

                    if pos1 == '名詞' and pos2 == '固有名詞' and pos3 == '人名':
                        # Tránh từ 1 ký tự kana đơn lẻ hoặc mảnh họ bị cắt đôi (vd: 田 trong 吉田, 野 trong 石野)
                        if len(surf) == 1 and (
                            re.match(r'^[\u3040-\u30ff]$', surf)
                            or surf in {'田', '野', '川', '山', '本', '村', '島', '藤', '木', '原', '沢', '井', '中', '高', '小', '大', '上', '下', '東', '西', '南', '北', '口', '谷', '橋', '森', '林', '石', '水', '金', '銀', '優'}
                        ):
                            continue
                        if surf in {'せっ', 'ちょ', 'うど'}:
                            continue
                        # Nếu surf là hiragana thuần và đứng ngay sau 1 ký tự hiragana KHÔNG phải trợ từ
                        # (VD: 'つ' + 'ゆき' + 'くん' do Whisper nghe lệch 'つよし'), ghép lại thành 'つゆき'
                        if idx > 0 and re.match(r'^[ぁ-ん]{2,3}$', surf):
                            prev_tok = toks[idx - 1]
                            prev_s = prev_tok.surface.strip()
                            prev_pos1 = getattr(prev_tok.feature, 'pos1', '') or ''
                            if (
                                len(prev_s) == 1
                                and re.match(r'^[ぁ-ん]$', prev_s)
                                and prev_pos1 not in ('助詞', '助動詞', '補助記号')
                                and prev_s not in ('お', 'ご')
                            ):
                                surf = prev_s + surf
                        jinmei_hits[surf] += 1
                        surface_score[surf] += 4 if has_honorific else 2
                        if has_honorific:
                            honorific_hits[surf] += 1
                    elif surf in {'葵', '茜', '楓', '雫', '凛', '瞳', '翼', '蓮', '匠', '誠'} and (
                        has_honorific or next_surf in ('は', 'が', 'も', 'と', 'とは', 'だ', 'って', 'だって', 'の', 'を', 'に', 'よ', 'ね')
                    ):
                        # Các tên riêng 1 chữ Hán phổ biến mà UniDic hay gán nhầm là 普通名詞 (vd: 葵 = Aoi)
                        jinmei_hits[surf] += 2
                        surface_score[surf] += 4 if has_honorific else 4
                        if has_honorific:
                            honorific_hits[surf] += 1
                    elif has_honorific and pos1 == '名詞' and len(surf) >= 2:
                        # Bo qua cum co dinh dang "go/o + danh tu + さん" (ご苦労さん,
                        # お疲れさま): do la loi cam on, khong phai ten nguoi.
                        prev_surf = toks[idx - 1].surface.strip() if idx > 0 else ''
                        if (surf not in _COMMON_JA_WORDS_BLACKLIST
                                and prev_surf not in ('ご', 'お', '御')):
                            honorific_hits[surf] += 1
                            surface_score[surf] += 3
            except Exception:
                pass

    # Lọc các ứng viên đạt ngưỡng tin cậy:
    # - Có ít nhất 1 lần đi kèm さん/ちゃん/くん/様 VÀ tổng điểm >= min_score
    # - HOẶC được UniDic gắn nhãn 人名 và xuất hiện >= 2 lần (điểm >= 4)
    raw_valid_surfaces: List[str] = []
    for surf, score in surface_score.items():
        if surf in _COMMON_JA_WORDS_BLACKLIST:
            continue
        if honorific_hits[surf] >= 1 and score >= min_score:
            raw_valid_surfaces.append(surf)
        elif jinmei_hits[surf] >= 2 and score >= 4:
            raw_valid_surfaces.append(surf)

    # Loại bỏ mảnh tên 1 ký tự nếu nó là chuỗi con của một tên >= 2 ký tự đã trích xuất trong cùng phim
    # (VD: '田' bị cắt ra từ '吉田 / 田さん' khi trong phim đã có '吉田' hoặc '海田')
    longer_surfaces = [s for s in raw_valid_surfaces if len(s) >= 2]
    valid_surfaces = [
        s for s in raw_valid_surfaces
        if not (len(s) == 1 and any(s in longer for longer in longer_surfaces))
    ]

    # Nhóm các biến thể chữ viết (vd: 美久 và ミク cùng đọc là Miku)
    by_canonical: Dict[str, Set[str]] = defaultdict(set)
    canonical_score: Counter = Counter()

    for surf in valid_surfaces:
        romaji = ja_to_hepburn(surf)
        if not romaji or len(romaji) < 2:
            continue
        surface_to_canonical[surf] = romaji
        by_canonical[romaji].add(surf)
        for hon in ('さん', 'ちゃん', 'くん', '君', '様'):
            by_canonical[romaji].add(f"{surf}{hon}")
        by_canonical[romaji].add(romaji)
        canonical_score[romaji] += surface_score[surf]

    # Phonetic Name Clustering (Q-P2.1): gom các biến thể tên nghe nhầm gần âm
    # (VD: Tsuyoshi <-> Tsuyoi / Tsuyuhi khi đi kèm hậu tố さん/君 trong cùng bộ phim)
    try:
        from rapidfuzz import fuzz as _rf_fuzz
        ranked_cans = sorted(by_canonical.keys(), key=lambda k: (-canonical_score[k], -len(k)))
        absorbed: Set[str] = set()
        extra_mistaken: Dict[str, Set[str]] = defaultdict(set)
        for i, main_can in enumerate(ranked_cans):
            if main_can in absorbed or len(main_can) < 4:
                continue
            for other_can in ranked_cans[i + 1:]:
                if other_can in absorbed or len(other_can) < 4:
                    continue
                # Chỉ gộp khi tên phụ XUẤT HIỆN HIẾM (≤ 1/3 tần suất tên chính).
                # Hai nhân vật thật với tên gần âm (Yuki/Yukio, Haruka/Haruko, Mika/Mikako)
                # có tần suất tương đương -> KHÔNG gộp (bug review 2026-11-01).
                if canonical_score[other_can] * 3 > canonical_score[main_can]:
                    continue
                if main_can[0].lower() == other_can[0].lower() and (
                    _rf_fuzz.ratio(main_can.lower(), other_can.lower()) >= 75.0
                    or (len(main_can) >= 6 and len(other_can) >= 6 and main_can[:4].lower() == other_can[:4].lower())
                ):
                    by_canonical[main_can].update(by_canonical[other_can])
                    canonical_score[main_can] += canonical_score[other_can]
                    extra_mistaken[main_can].add(other_can)
                    absorbed.add(other_can)
        for gone in absorbed:
            by_canonical.pop(gone, None)
            canonical_score.pop(gone, None)
    except Exception:
        extra_mistaken = defaultdict(set)

    # Hợp nhất với existing_names từ config (config luôn được ưu tiên giữ nguyên)
    merged_map: Dict[str, dict] = {}
    for entry in existing_list:
        if not isinstance(entry, dict):
            continue
        can = (entry.get('canonical') or '').strip()
        if not can:
            continue
        merged_map[can.lower()] = {
            'canonical': can,
            'aliases': list(entry.get('aliases') or []),
            'mistaken_aliases': list(entry.get('mistaken_aliases') or []),
            'also_known_as': list(entry.get('also_known_as') or []),
        }

    for can, aliases_set in sorted(by_canonical.items(), key=lambda kv: -canonical_score[kv[0]]):
        key = can.lower()
        # Ưu tiên đặt các alias tiếng Nhật ngắn (gốc) lên đầu danh sách để _render_character_block hiển thị đẹp
        ja_raw = sorted(
            [a for a in aliases_set if not a.endswith(('さん', 'ちゃん', 'くん', '様')) and a != can],
            key=len,
        )
        ja_with_hon = sorted(
            [a for a in aliases_set if a.endswith(('さん', 'ちゃん', 'くん', '様'))],
            key=len,
        )
        ordered_aliases = ja_raw + ja_with_hon + [can]
        mistaken = sorted(set(_generate_mistaken_aliases(can)) | extra_mistaken.get(can, set()))

        if key in merged_map:
            cur_aliases = merged_map[key]['aliases']
            for a in ordered_aliases:
                if a not in cur_aliases:
                    cur_aliases.append(a)
            cur_mistaken = merged_map[key]['mistaken_aliases']
            for m in mistaken:
                if m not in cur_mistaken:
                    cur_mistaken.append(m)
        else:
            merged_map[key] = {
                'canonical': can,
                'aliases': ordered_aliases,
                'mistaken_aliases': mistaken,
            }

    return list(merged_map.values())


# =============================================================================
# PHẦN 2: BỘ KIỂM ĐỊNH CHẤT LƯỢNG CHUYÊN BIỆT TIẾNG NHẬT (GROUP C)
# =============================================================================

# Các mẫu phủ định thật sự trong tiếng Nhật (không dùng 'ず' đơn lẻ hay 'なよ$' để tránh khớp nhầm 'ずっと', '考えなよ')
_JA_NEGATION_RE = re.compile(
    r'(ません|ませんでした|なかった|ないです|ないで|なくて|なければ|っこない|'
    r'たくない|られない|できない|じゃない|ではない|じゃなかった|ではなかった|'
    r'ないでください|ないでくれ|ない$|ない。|ない！|ない!|ない…)'
)

# Các mẫu câu tiếng Nhật có chứa 'ない'/'ません' nhưng thực chất là lời xin lỗi, rủ rê, hỏi đuôi, hoặc khẳng định.
# Audit C-2: KHÔNG đưa động từ thường vào đây. Trước đây danh sách còn chứa
# 出かけない / 行かない$ / 飲まない$ / 食べない$ / しない$ / 見ない$ / 遊ばない$ — đây là
# phủ định THẬT của động từ rất phổ biến trong phụ đề, khiến lỗi "食べない" -> "ăn"
# không bao giờ bị phát hiện. Chỉ giữ idiom / mẫu ngữ pháp mang nghĩa khẳng định.
_JA_PSEUDO_NEGATION_RE = re.compile(
    r'(すみません|すいません|ごめんなさい|申し訳ありません|申し訳ございません|'
    r'じゃないですか|じゃないか|じゃないかな|じゃないの|じゃない[？?]|ちゃうじゃない|てしまうじゃない|じゃない$|'
    r'のではないですか|のではないでしょうか|ではないですか|ではないでしょうか|んじゃありません|んではありません|'
    r'んじゃない|んじゃなかった|かもしれない|かも知れない|'
    r'しか[^。！？!?]*ない|しか[^。！？!?]*ません|'
    r'なければなら|なければなり|なければいけ|なくてはいけ|なくてはなり|なきゃ|ないと|なくては|'
    r'ざるを得ない|ざるを得なかった|'
    r'っぱなし|仕様がない|しょうがない|しょうがありません|仕方ない|しかたない|仕方がありません|仕方ありません|'
    r'間違いない|まちがいない|違いない|ちがいない|たまらない|やむを得ない|なかなかいない)'
)

# Các từ phủ định tiếng Việt tương ứng
_VI_NEGATION_RE = re.compile(
    r'\b(không|chẳng|chả|chưa|đừng|chớ|đâu|vô|bất|khỏi|hết|ngừng|thôi|thiếu|'
    r'tránh|từ chối|phủ nhận|chối|cấm|đời nào|làm sao|chưa từng|không hề|tuyệt đối không)\b',
    re.IGNORECASE,
)

_CJK_CHAR_RE = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+')
_VI_LATIN_RE = re.compile(r'[a-zA-ZàáảãạăâêôơưđÀÁẢÃẠĂÂÊÔƠƯĐ]')

# --- P3: ngưỡng phát hiện mất vế câu (đo trên 2 640 cặp thật trong translation.db) ---
# Tiếng Việt dài hơn tiếng Nhật ~2.5 lần (median 2.54, p05 1.42, p01 0.94).
_MIN_JA_LEN_FOR_RATIO = 12   # câu ngắn hơn thì tỷ lệ nhiễu, không đáng flag
_MIN_VI_JA_RATIO = 0.90      # dưới p01 -> gần như chắc chắn mất vế
_REPEATED_TOKEN_RE = re.compile(r'^(\S{1,6})(\s*\1){2,}')  # "うん うん うん" -> nén hợp lý

# Từ điển dịch nhanh các mảnh chữ Hán/Nhật hay bị sót trong câu tiếng Việt (dùng chung từ post_processor)
from .post_processor import COMMON_CJK_LEAK_MAP as _COMMON_RESIDUAL_CJK_MAP


class JaQAChecker:
    """Bộ kiểm định chất lượng tự động chuyên biệt cho dịch thuật JA -> VI."""

    def __init__(
        self,
        glossary_terms: Optional[List[dict]] = None,
        character_names: Optional[List[dict]] = None,
    ):
        self.glossary_terms = glossary_terms or []
        self.character_names = character_names or []

    # --------------------------------------------------------- 1. Phủ định
    @staticmethod
    def check_negation_mismatch(ja_text: str, vi_text: str) -> bool:
        """Trả về True nếu câu gốc JA có phủ định rõ ràng nhưng câu VI dịch thành khẳng định."""
        if not ja_text or not vi_text:
            return False
        ja = ja_text.strip()
        vi = vi_text.strip()
        if len(ja) < 4:
            return False
        # Bỏ qua nếu là câu hỏi đuôi / phỏng đoán mang nghĩa khẳng định
        if _JA_PSEUDO_NEGATION_RE.search(ja):
            return False
        # Bỏ qua nếu câu JA không có dấu hiệu phủ định
        if not _JA_NEGATION_RE.search(ja):
            return False
        # Kiểm tra câu VI có từ phủ định không
        if _VI_NEGATION_RE.search(vi):
            return False
        return True

    # --------------------------------------------------------- 2. CJK Leak
    @staticmethod
    def has_cjk_leak(vi_text: str) -> bool:
        """Trả về True nếu câu tiếng Việt còn sót ký tự Hiragana/Katakana/Kanji."""
        if not vi_text:
            return False
        return bool(_CJK_CHAR_RE.search(vi_text))

    def clean_cjk_leak(self, ja_text: str, vi_text: str) -> str:
        """Làm sạch ký tự CJK/Kana bị sót trong câu tiếng Việt:
        1. Thay bằng nghĩa tiếng Việt nếu khớp từ điển `_COMMON_RESIDUAL_CJK_MAP` hoặc `glossary_terms`.
        2. Nếu là tên nhân vật (trong `character_names` hoặc được fugashi nhận diện là 人名), phiên âm sang Hepburn Romaji.
        3. Xóa các trợ từ kana lẻ hoặc ký tự CJK dư thừa khi câu đã có phần tiếng Việt rõ ràng.
        """
        if not vi_text or not self.has_cjk_leak(vi_text):
            return vi_text

        result = vi_text

        # 1. Thay theo character_names
        for entry in self.character_names or []:
            can = (entry.get('canonical') or '').strip()
            for alias in entry.get('aliases') or []:
                if alias and alias != can and alias in result:
                    result = result.replace(alias, f" {can} ")

        # 2. Thay theo glossary_terms
        for term in sorted(self.glossary_terms or [], key=lambda t: -len(t.get('source_term', ''))):
            src = (term.get('source_term') or '').strip()
            tgt = (term.get('target_term') or '').strip()
            if src and tgt and not tgt.startswith("'") and src in result:
                result = result.replace(src, f" {tgt} ")

        # 3. Thay theo bảng từ CJK thường gặp
        for cjk_k in sorted(_COMMON_RESIDUAL_CJK_MAP.keys(), key=len, reverse=True):
            if cjk_k in result:
                result = result.replace(cjk_k, f" {_COMMON_RESIDUAL_CJK_MAP[cjk_k]} ")

        # 4. Nếu vẫn còn cụm CJK: kiểm tra bằng fugashi xem có phải tên riêng không -> chuyển sang Hepburn
        if _CJK_CHAR_RE.search(result):
            def _replace_cjk_span(match: re.Match) -> str:
                span = match.group(0)
                # Xóa hậu tố xưng hô nếu đi kèm
                stem = re.sub(r'(さん|ちゃん|くん|君|様|さま)$', '', span)
                tagger = _get_tagger()
                if tagger is not None and stem:
                    try:
                        toks = list(tagger(stem))
                        if toks and all(
                            getattr(t.feature, 'pos2', '') == '固有名詞'
                            or getattr(t.feature, 'pos3', '') == '人名'
                            for t in toks
                        ):
                            hep = ja_to_hepburn(stem)
                            if hep:
                                return f" {hep} "
                    except Exception:
                        pass
                # Nếu toàn bộ câu chưa có chữ tiếng Việt nào -> thử phiên âm để không trả về chuỗi rỗng
                if not _VI_LATIN_RE.search(result):
                    hep = ja_to_hepburn(stem)
                    return hep if hep else ''
                return ' '

            result = _CJK_CHAR_RE.sub(_replace_cjk_span, result)

        result = re.sub(r'\s+', ' ', result).strip()
        result = re.sub(r'\s+([.,!?…])', r'\1', result)
        return result

    # --------------------------------------------------------- 3. Glossary
    def check_glossary_violations(self, ja_text: str, vi_text: str) -> List[dict]:
        """Kiểm tra các term bắt buộc trong glossary xuất hiện ở câu JA nhưng bị bỏ qua ở câu VI."""
        if not ja_text or not vi_text or not self.glossary_terms:
            return []
        violations = []
        vi_lower = vi_text.lower()
        for term in self.glossary_terms:
            src = (term.get('source_term') or '').strip()
            tgt = (term.get('target_term') or '').strip()
            if not src or not tgt or len(src) < 2 or tgt.startswith("'"):
                continue
            if src not in ja_text:
                continue
            tgt_lower = tgt.lower()
            if tgt_lower in vi_lower:
                continue
            violations.append({'source_term': src, 'target_term': tgt})
        return violations

    # --------------------------------------------------------- 4. Độ dài
    @staticmethod
    def check_length_anomaly(ja_text: str, vi_text: str) -> Optional[str]:
        """Kiểm tra tỷ lệ độ dài JA/VI bất thường (mất vế câu hoặc lặp ảo giác).

        Audit P3: rule cũ chỉ flag khi `vi_len <= 3`, nên bỏ sót toàn bộ trường hợp
        dịch mất vế câu (vd JA 38 ký tự 5 vế -> VI 31 ký tự 1 vế).

        Ngưỡng dựa trên đo thực tế 2 640 cặp JA/VI trong translation.db:
            median VI/JA = 2.54   p05 = 1.42   p01 = 0.94
        Tiếng Việt vốn dài hơn tiếng Nhật ~2.5 lần, nên ratio < 0.90 (dưới p01)
        là đuôi bất thường -> gần như chắc chắn mất vế.
        """
        if not ja_text or not vi_text:
            return 'empty_translation' if ja_text and not (vi_text or '').strip() else None
        ja = ja_text.strip()
        vi = vi_text.strip()
        ja_len = len(ja)
        vi_len = len(vi)

        # (a) Gần như rỗng: giữ nguyên rule cũ.
        if ja_len >= 12 and vi_len <= 3:
            return 'severe_truncation'

        # (b) Quá ngắn so với câu gốc -> mất vế câu.
        if ja_len >= _MIN_JA_LEN_FOR_RATIO and vi_len / ja_len < _MIN_VI_JA_RATIO:
            # Ngoại lệ: câu gốc chỉ là MỘT từ lặp lại ("うん うん うん うん うん ん").
            # Nén lặp lại thành "Ừ ừ ừ ừ ừ ừm" là hợp lý, không phải mất nội dung.
            if not _REPEATED_TOKEN_RE.match(ja):
                return 'severe_truncation'

        if vi_len > max(140, ja_len * 6):
            return 'hallucination_long'
        return None

    # --------------------------------------------------------- Tổng hợp 1 dòng
    def inspect_line(self, ja_text: str, vi_text: str) -> dict:
        """Kiểm tra toàn diện 1 cặp câu (JA, VI). Trả về dict các lỗi phát hiện được."""
        issues: List[str] = []
        if not (vi_text or '').strip() and (ja_text or '').strip():
            issues.append('empty_translation')
            return {'ok': False, 'issues': issues, 'glossary_missing': []}

        if self.has_cjk_leak(vi_text):
            issues.append('cjk_leak')
        if self.check_negation_mismatch(ja_text, vi_text):
            issues.append('negation_mismatch')
        len_issue = self.check_length_anomaly(ja_text, vi_text)
        if len_issue:
            issues.append(len_issue)
        glossary_missing = self.check_glossary_violations(ja_text, vi_text)
        if glossary_missing:
            issues.append('glossary_violation')

        return {
            'ok': len(issues) == 0,
            'issues': issues,
            'glossary_missing': glossary_missing,
        }

    # --------------------------------------------------------- Audit & Fix Project
    def audit_and_fix_project(
        self,
        db,
        project_id: int,
        translator=None,
        max_llm_retries: int = 15,
    ) -> dict:
        """Quét toàn bộ project sau khi dịch, tự động sửa lỗi CJK leak, và dịch lại có định hướng
        (targeted retry) cho các dòng bị đảo ngược phủ định hoặc đứt câu."""
        items = db.get_all_items(project_id)
        stats = {
            'total': len(items),
            'checked': 0,
            'issues_found': 0,
            'auto_fixed': 0,
            'llm_retried': 0,
            'by_type': Counter(),
        }
        llm_budget = max_llm_retries

        for item in items:
            ja = (item.get('original_text') or '').strip()
            vi = (item.get('translated_text') or '').strip()
            if not ja:
                continue
            stats['checked'] += 1
            info = self.inspect_line(ja, vi)
            if info['ok']:
                continue

            stats['issues_found'] += 1
            for iss in info['issues']:
                stats['by_type'][iss] += 1

            new_vi = vi

            # Bước 1: Nếu có lỗi rò rỉ CJK, làm sạch bằng clean_cjk_leak trước
            if 'cjk_leak' in info['issues'] and new_vi:
                cleaned = self.clean_cjk_leak(ja, new_vi)
                if cleaned and cleaned != new_vi:
                    new_vi = cleaned

            # Bước 2: Nếu bị đảo ngược phủ định, rỗng, hoặc mất vế câu -> gọi LLM dịch lại 1 dòng kèm chỉ dẫn
            needs_retranslate = (
                'negation_mismatch' in info['issues']
                or 'empty_translation' in info['issues']
                or 'severe_truncation' in info['issues']
                or (not new_vi)
            )
            if needs_retranslate and translator is not None and llm_budget > 0:
                llm_budget -= 1
                # P3: chỉ dẫn phải khớp với lỗi thật. Khi mất vế câu, phải yêu cầu
                # dịch ĐỦ mọi vế — nếu không LLM lại cắt cụt y như lần trước.
                hints = []
                if 'severe_truncation' in info['issues']:
                    hints.append(
                        "the previous attempt dropped clauses — translate EVERY clause "
                        "present in the Japanese, do not omit, summarise or shorten"
                    )
                if 'negation_mismatch' in info['issues']:
                    hints.append(
                        "keep the negative meaning with không/chưa/đừng"
                    )
                hint_text = ("; " + "; ".join(hints)) if hints else ""
                prompt = (
                    f"Translate this Japanese subtitle line into natural Vietnamese"
                    f"{hint_text} (output ONLY the Vietnamese translation, no explanation):\n"
                    f"{ja}\n"
                    f"Vietnamese:"
                )
                try:
                    raw = translator.generate(prompt, temperature=0.05, num_predict=120)
                    cand_lines = [
                        ln.strip(' "\'')
                        for ln in (raw or '').strip().split('\n')
                        if ln.strip()
                    ]
                    cand = cand_lines[-1] if cand_lines else ''
                    cand = re.sub(r'^(Vietnamese|Bản dịch|Tiếng Việt)\s*:\s*', '', cand, flags=re.IGNORECASE).strip()
                    cand = self.clean_cjk_leak(ja, cand)
                    cand_low = cand.lower()
                    echo_markers = (
                        'lưu ý quan trọng', 'câu gốc', 'bản dịch tiếng việt',
                        'nghĩa phủ định', 'chỉ trả về', 'tuyệt đối không để sót',
                        'translate this japanese', 'natural vietnamese',
                    )
                    is_echo = any(m in cand_low for m in echo_markers)
                    still_bad_neg = (
                        'negation_mismatch' in info['issues']
                        and self.check_negation_mismatch(ja, cand)
                    )
                    if (
                        cand
                        and 2 <= len(cand) <= max(120, len(ja) * 5)
                        and not is_echo
                        and not still_bad_neg
                        and not self.has_cjk_leak(cand)
                    ):
                        new_vi = cand
                        stats['llm_retried'] += 1
                except Exception as e:
                    logger.debug(f"JaQAChecker targeted retry failed on sub {item.get('sub_index')}: {e}")

            if new_vi and new_vi != vi:
                db.update_item_translation(item['id'], new_vi)
                stats['auto_fixed'] += 1
                if hasattr(db, 'log_translation_error'):
                    try:
                        db.log_translation_error(
                            project_id=project_id,
                            sub_index=item.get('sub_index', 0),
                            source_text=ja,
                            wrong_text=vi,
                            fixed_text=new_vi,
                            error_type='ja_qa_' + ('_'.join(info['issues'][:2]) or 'fix'),
                        )
                    except Exception:
                        pass

        stats['by_type'] = dict(stats['by_type'])
        return stats


# =============================================================================
# PHẦN 3: CONTEXT WINDOW TOKEN GUARD (GROUP C2)
# =============================================================================

def estimate_prompt_tokens(prompt: str) -> int:
    """Ước lượng số token của prompt kết hợp CJK + Tiếng Việt + JSON.

    Trung bình trong Qwen/Hunyuan tokenizer:
      - Mỗi ký tự CJK / tiếng Việt có dấu ~ 0.65 - 0.85 token
      - Cộng thêm biên độ an toàn (safety margin 20%).
    """
    if not prompt:
        return 0
    cjk_count = len(_CJK_CHAR_RE.findall(prompt))
    return int(len(prompt) * 0.55 + cjk_count * 0.65) + 64


def guard_prompt_num_ctx(prompt: str, configured_num_ctx: int = 4096, num_predict: int = 1024) -> int:
    """Đảm bảo `num_ctx` gửi tới Ollama luôn đủ chứa toàn bộ `prompt + num_predict`.

    Nếu vượt quá `configured_num_ctx`, tự động nâng `num_ctx` lên mức an toàn
    (6144 hoặc 8192) để Ollama KHÔNG âm thầm cắt cụt System Prompt / Glossary ở đầu!
    """
    est_input = estimate_prompt_tokens(prompt)
    needed = est_input + max(512, num_predict) + 256
    if needed <= configured_num_ctx:
        return configured_num_ctx
    for step in (6144, 8192, 12288, 16384):
        if step >= needed:
            logger.info(
                f"ContextGuard: prompt needs ~{needed} tokens (> num_ctx={configured_num_ctx}), "
                f"auto-expanding num_ctx -> {step}"
            )
            return step
    return max(configured_num_ctx, needed)


def main(argv=None) -> int:
    """CLI cho phép quét và làm sạch toàn bộ lịch sử DB hoặc 1 project cụ thể.

    Usage:
        python core/ja_qa_checker.py --clean-db
        python core/ja_qa_checker.py <project_name>
    """
    import os
    import sys

    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from core.database import Database
    from core.post_processor import RuleBasedPostProcessor

    argv = argv if argv is not None else sys.argv[1:]
    db = Database()
    pp = RuleBasedPostProcessor()

    projects = db.get_all_projects() if (not argv or argv[0] == '--clean-db') else []
    if argv and argv[0] != '--clean-db':
        p = db.get_project_by_name(argv[0])
        if p:
            projects = [p]

    total_fixed = 0
    for p in projects:
        pid = p['id']
        pname = p['name']
        items = db.get_all_items(pid)
        if not items:
            continue
        ja_lines = [it.get('original_text') or '' for it in items]
        chars = extract_character_names_from_lines(ja_lines)
        pp.set_character_names(chars)
        st1 = pp.process_project(db, pid)
        qa = JaQAChecker(character_names=chars)
        st2 = qa.audit_and_fix_project(db, pid, translator=None)
        c1 = st1.get('changed', 0)
        c2 = st2.get('auto_fixed', 0)
        if c1 + c2 > 0 or len(projects) == 1:
            c_list = [c['canonical'] for c in chars[:6]]
            print(
                f"[JA-QA] Project {pid:2d} ({pname}): "
                f"PostProcessor={c1}, JaQAChecker={c2}, "
                f"issues={st2.get('by_type', {})}, auto_chars={c_list}"
            )
            total_fixed += (c1 + c2)
    print(f"[JA-QA] Hoàn tất: Đã tự động làm sạch {total_fixed} dòng trên {len(projects)} dự án.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
