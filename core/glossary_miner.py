# -*- coding: utf-8 -*-
"""Tự học từ vựng tình dục từ kết quả dịch, ghi ra file glossary riêng và DB.

Ý tưởng: sau mỗi lần dịch, DB lưu đủ cặp <original_text> (JA) /
<translated_text> (VI). Kết hợp phân tích hình thái học tiếng Nhật (fugashi)
và thống kê co-occurrence 2 chiều (PMI đơn giản) trên từng project và xuyên
suốt toàn bộ các project trong DB để tự học cặp từ uncen chuẩn xác.

Usage:
    python core/glossary_miner.py <project_name>      # mine 1 project
    python core/glossary_miner.py --all               # mine toàn bộ DB -> glossary_learned.yaml
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Set, Tuple

_TAGGER = None
_TAGGER_FAILED = False


def _get_tagger():
    """Lazy-init fugashi Tagger (MeCab + unidic-lite)."""
    global _TAGGER, _TAGGER_FAILED
    if _TAGGER is not None:
        return _TAGGER
    if _TAGGER_FAILED:
        return None
    try:
        import fugashi
        _TAGGER = fugashi.Tagger()
        return _TAGGER
    except Exception:
        _TAGGER_FAILED = True
        return None


# Từ khoá JA đặc trưng tình dục — cụm JA chứa 1 trong các từ này mới
# được xét làm candidate. Bám theo từ vựng đã có trong glossary_uncen.yaml.
_UNCEN_KEYWORDS = (
    # cơ thể / bộ phận
    'ペニス', 'ちんこ', 'ちんぽ', 'おちんちん', '亀頭', '膣', '阴道', '精液',
    '射精', '中出し', '仲出し', '母乳', '勃起', '肉体', '身体', '裸体', '裸',
    '乳房', 'おっぱい', '乳首', 'まんこ', 'おまんこ', '性器', '陰部', 'アナル',
    'お尻', '太もも',
    # hành động tình dục
    'セックス', 'フェラ', 'クンニ', 'パイズリ', '手コキ', 'オナニー', '挿入',
    'いじる', '弄ぶ', 'イく', 'イキ', 'イク', '絶頂', '潮吹き', '犯す',
    '舐め', '咥え', '濡れ', '喘ぎ', '喘ぐ', '搾り', 'キス', '性処理',
    # cảm giác / trạng thái
    '気持ちいい', '気持ち良い', '気持ち良', '気持ちよ', '我慢', '興奮', '敏感',
    '変態', 'いやらしい', 'エロ', '恥ずかしい',
    # quan hệ / tình trạng
    'セフレ', '愛人', '夫婦', '不倫', '浮気', '義理',
    '寝取', '義母', '義姉', '義父', '義兄', '義弟', '義妹', '人妻',
    '旦那', '主人', '彼氏', '彼女', '恋人', '抱き', '抱く', '妻', '婚約',
    # khái niệm
    '性欲', '快感', '性癖', '色情', '凌辱', '痴漢', '輪姦', '強姦',
    '調教', '拘束', '体位', 'バック', 'ゴム', '避妊', '妊娠', '孕む',
)

_UNCEN_KEYWORDS = tuple(
    kw for kw in _UNCEN_KEYWORDS
    if len(kw) >= 2 and re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', kw)
)

_JA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]')
_VI_RE = re.compile(r'[a-zA-ZàáảãạăâêôơưđÀÁẢÃẠĂÂÊÔƠƯĐ]')

# Trợ từ / dấu câu đầu hoặc cuối cụm JA cần cắt bỏ để không học nhầm "旦那さんと"
_JA_BOUNDARY_PARTICLES_RE = re.compile(
    r'^[とはがをにへでものやねよかなわぞさ、。！？!?\s]+|[とはがをにへでものやねよかなわぞさ、。！？!?\s]+$'
)

# Từ tiếng Việt quá chung hoặc từ mệnh lệnh/hư từ — không được làm target
_VI_STOPWORDS = {
    'anh', 'em', 'chị', 'cô', 'bạn', 'tôi', 'chúng', 'mình', 'họ', 'nó',
    'có', 'không', 'là', 'gì', 'vậy', 'được', 'đang', 'sẽ', 'với', 'cho',
    'của', 'trong', 'như', 'mà', 'thì', 'ra', 'vào', 'này', 'đó', 'khi',
    'đã', 'cũng', 'rất', 'lại', 'thế', 'nào', 'sao', 'hả', 'à', 'ơi',
    'con', 'bị', 'bởi', 'theo', 'từ', 'đến', 'sau', 'trước', 'về',
    'một', 'hai', 'ba', 'này', 'nọ', 'ấy', 'yêu', 'làm', 'nói', 'thấy',
    'biết', 'muốn', 'phải', 'vẫn', 'nữa', 'bên', 'hãy', 'đừng', 'chớ',
    'rồi', 'nhé', 'nha', 'chứ', 'đâu', 'thôi', 'nào', 'đi', 'quá', 'lắm',
    'hơn', 'nhất', 'cứ', 'sắp', 'vừa', 'mới', 'nhỉ', 'mất', 'luôn', 'đấy', 'đây',
    'mày', 'tao', 'tớ', 'cậu', 'hắn', 'ông', 'bà', 'ta', 'mình', 'cái', 'thể',
}

_JA_MIN, _JA_MAX = 2, 8
_VI_MIN, _VI_MAX = 1, 3
_VI_MIN_TARGET_WORDS = 2


class GlossaryMiner:
    """Suy ra glossary term từ cặp (JA, VI) đã dịch trong DB."""

    def __init__(self, db, config: dict):
        self.db = db
        self.config = config
        pipe = config.get('pipeline', {}) or {}
        self.min_freq = pipe.get('learn_glossary_min_freq', 3)
        self.min_pair_ratio = pipe.get('learn_glossary_min_ratio', 0.5)
        self.max_candidates = pipe.get('learn_glossary_max_candidates', 50)
        self.existing = self._existing_terms()

    # ---------------------------------------------------------------- mine
    def mine(self, project_id: int, skip_existing: bool = True) -> List[dict]:
        """Trả về list candidate {source_term, target_term, freq, confidence} cho 1 project."""
        items = self.db.get_all_items(project_id)
        pairs = [
            (it.get('original_text') or '', it.get('translated_text') or '', project_id)
            for it in items
            if it.get('translated_text')
        ]
        return self._mine_from_pairs(pairs, skip_existing=skip_existing)

    def mine_all_projects(self, skip_existing: bool = True) -> List[dict]:
        """Học từ vựng xuyên suốt TẤT CẢ các project đã dịch trong DB."""
        if not hasattr(self.db, 'get_all_projects'):
            return []
        projects = self.db.get_all_projects()
        all_pairs: List[Tuple[str, str, int]] = []
        for p in projects:
            pid = p['id']
            for it in self.db.get_all_items(pid):
                if it.get('translated_text'):
                    all_pairs.append((
                        it.get('original_text') or '',
                        it.get('translated_text') or '',
                        pid,
                    ))
        cands = self._mine_from_pairs(all_pairs, skip_existing=skip_existing)
        # Lưu các candidate chất lượng cao (xuất hiện ở >= 2 phim hoặc freq >= 5, conf >= 0.75) vào DB
        if hasattr(self.db, 'upsert_learned_term'):
            for c in cands:
                if c['confidence'] >= 0.75 and (c.get('project_count', 1) >= 2 or c['freq'] >= 5):
                    self.db.upsert_learned_term(
                        source_term=c['source_term'],
                        target_term=c['target_term'],
                        freq=c['freq'],
                        confidence=c['confidence'],
                        project_count=c.get('project_count', 1),
                        context_hint=(
                            f"auto-learned (freq={c['freq']}, "
                            f"films={c.get('project_count', 1)}, "
                            f"confidence={c['confidence']})"
                        ),
                    )
        return cands

    def _mine_from_pairs(self, pairs: List[Tuple[str, str, int]], skip_existing: bool = True) -> List[dict]:
        if not pairs:
            return []

        ja_count: Counter = Counter()
        vi_count: Counter = Counter()
        pair_count: Counter = Counter()
        pair_projects: Dict[Tuple[str, str], Set[int]] = defaultdict(set)

        for ja_text, vi_text, pid in pairs:
            ja_ngrams = self._ja_ngrams(ja_text)
            vi_ngrams = self._vi_ngrams(vi_text)
            if not ja_ngrams or not vi_ngrams:
                continue
            for j in ja_ngrams:
                ja_count[j] += 1
            for v in vi_ngrams:
                vi_count[v] += 1
            for j in ja_ngrams:
                for v in vi_ngrams:
                    pair_count[(j, v)] += 1
                    pair_projects[(j, v)].add(pid)

        by_ja: Dict[str, List] = defaultdict(list)
        for (j, v), n in pair_count.items():
            by_ja[j].append((v, n))

        candidates = []
        for ja, freq in ja_count.items():
            if freq < self.min_freq:
                continue
            if skip_existing and self._is_already_covered(ja, ''):
                continue

            best_v, best_n, best_score = None, 0, 0.0
            for v, n in by_ja[ja]:
                if n < self.min_freq:
                    continue
                score = min(n / freq, n / vi_count[v])
                if score > best_score or (score == best_score and len(v) > len(best_v or '')):
                    best_v, best_n, best_score = v, n, score
            if not best_v or best_score < self.min_pair_ratio:
                continue

            if len(best_v.split()) < _VI_MIN_TARGET_WORDS:
                longer = [
                    (v, n) for v, n in by_ja[ja]
                    if len(v.split()) >= _VI_MIN_TARGET_WORDS
                    and min(n / freq, n / vi_count[v]) >= self.min_pair_ratio
                ]
                if longer:
                    best_v, best_n = max(longer, key=lambda t: (len(t[0]), t[1]))

            orig_best_v = best_v
            # Cắt hư từ/giới từ dính ở đầu hoặc cuối cụm tiếng Việt
            best_v = re.sub(
                r'^(hãy|đừng|chớ|đang|đã|sẽ|vẫn|cứ|cùng|rất)\s+|\s+(của|là|cho|với|ở|như|và|hay|được|bị|rồi|nhé|nha|đi|mà|đâu|thôi|quá|lắm)$',
                '',
                best_v,
                flags=re.IGNORECASE,
            ).strip(' .,!?;:"\'()[]')
            if not best_v or best_v.lower() in _VI_STOPWORDS:
                continue

            if not self._looks_vietnamese(best_v):
                continue
            if skip_existing and self._is_already_covered(ja, best_v):
                continue
            candidates.append({
                'source_term': ja,
                'target_term': best_v,
                'freq': best_n,
                'project_count': len(pair_projects.get((ja, orig_best_v), {1})),
                'confidence': round(best_score, 2),
            })

        candidates.sort(key=lambda c: (-c.get('project_count', 1), -c['freq'], -c['confidence']))
        return self._dedupe_substrings(candidates)[:self.max_candidates]

    @staticmethod
    def _dedupe_substrings(candidates: List[dict]) -> List[dict]:
        """Loại n-gram là substring của n-gram khác cùng target."""
        candidates = sorted(candidates, key=lambda c: -len(c['source_term']))
        kept: List[dict] = []
        for c in candidates:
            src, tgt = c['source_term'], c['target_term']
            if any(k['target_term'] == tgt and src in k['source_term']
                   for k in kept):
                continue
            kept.append(c)
        return kept

    # ------------------------------------------------------------ n-grams
    def _ja_ngrams(self, text: str) -> Set[str]:
        """Trích cụm JA chứa từ khoá uncen (kết hợp fugashi + n-gram đã cắt trợ từ)."""
        s = re.sub(r'\s+', '', text.strip())
        if not s:
            return set()
        out: Set[str] = set()

        # 1. Phân tích hình thái học bằng fugashi (nếu có) để lấy từ nguyên thể / cụm danh từ sạch
        tagger = _get_tagger()
        if tagger is not None:
            try:
                tokens = list(tagger(s))
                for i, tok in enumerate(tokens):
                    pos1 = getattr(tok.feature, 'pos1', '') or ''
                    if pos1 in ('助詞', '助動詞', '補助記号', '空白', '接尾辞'):
                        continue
                    surf = tok.surface
                    orth = getattr(tok.feature, 'orthBase', None) or surf
                    # Với động từ/tính từ ưu tiên dạng nguyên thể (orthBase) để không dính đuôi chia
                    forms = (orth,) if pos1 in ('動詞', '形容詞') else (surf, orth)
                    for form in forms:
                        if _JA_MIN <= len(form) <= _JA_MAX and any(kw in form for kw in _UNCEN_KEYWORDS):
                            out.add(form)
                    # Cụm danh từ ghép 2..3 token liên tiếp (không lấy trợ từ hay động từ chia dở)
                    if pos1 in ('名詞', '接頭辞', '形状詞'):
                        acc = surf
                        for j in range(i + 1, min(i + 4, len(tokens))):
                            next_tok = tokens[j]
                            next_pos = getattr(next_tok.feature, 'pos1', '') or ''
                            if next_pos not in ('名詞', '形状詞'):
                                break
                            acc += next_tok.surface
                            if len(acc) > _JA_MAX:
                                break
                            if any(kw in acc for kw in _UNCEN_KEYWORDS):
                                out.add(acc)
            except Exception:
                pass
            for kw in _UNCEN_KEYWORDS:
                if kw in s:
                    out.add(kw)
            return out

        # 2. Fallback Character n-grams khi không có fugashi
        for n in range(_JA_MIN, _JA_MAX + 1):
            for i in range(len(s) - n + 1):
                gram = s[i:i + n]
                cleaned = _JA_BOUNDARY_PARTICLES_RE.sub('', gram)
                if len(cleaned) < _JA_MIN or not _JA_RE.search(cleaned):
                    continue
                if any(kw in cleaned for kw in _UNCEN_KEYWORDS):
                    out.add(cleaned)
        return out

    @staticmethod
    def _vi_ngrams(text: str) -> Set[str]:
        """n-gram từ của text Việt, loại n-gram toàn từ stopword."""
        words = [w.strip('.,!?;:"\'()[]') for w in re.split(r'\s+', text.strip()) if w.strip('.,!?;:"\'()[]')]
        out: Set[str] = set()
        for n in range(_VI_MIN, _VI_MAX + 1):
            for i in range(len(words) - n + 1):
                slice_words = words[i:i + n]
                toks = [w.lower() for w in slice_words]
                if all(t in _VI_STOPWORDS for t in toks):
                    continue
                if toks[0] in _VI_STOPWORDS or toks[-1] in _VI_STOPWORDS or 'của' in toks:
                    continue
                out.add(' '.join(slice_words))
        return out

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _looks_vietnamese(text: str) -> bool:
        """Target phải trông như tiếng Việt, không phải kana/kanji."""
        if not text or not text.strip():
            return False
        if _JA_RE.search(text):
            return False
        return bool(_VI_RE.search(text))

    def _existing_terms(self) -> Set[str]:
        """Tập source_term đã có trong các file glossary (không học lại)."""
        existing: Set[str] = set()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ('glossary_uncen.yaml', 'glossary_common.yaml'):
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            try:
                import yaml
                with open(path, encoding='utf-8') as f:
                    raw = yaml.safe_load(f) or []
                for e in raw:
                    if isinstance(e, dict):
                        src = (e.get('source_term') or e.get('source') or '').strip()
                        if src:
                            existing.add(src)
            except Exception:
                continue
        return existing

    def _is_already_covered(self, ja: str, vi: str) -> bool:
        if ja in self.existing:
            return True
        if ja not in _UNCEN_KEYWORDS:
            for term in self.existing:
                if term and len(term) >= 2 and term in ja:
                    return True
        return bool(vi and vi in self._existing_targets)

    @property
    def _existing_targets(self) -> Set[str]:
        return self.__dict__.setdefault('_targets_cache', self._load_targets())

    def _load_targets(self) -> Set[str]:
        pipe = (self.config or {}).get('pipeline', {}) or {}
        if not pipe.get('block_existing_targets', False):
            return set()
        targets: Set[str] = set()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ('glossary_uncen.yaml', 'glossary_common.yaml'):
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            try:
                import yaml
                with open(path, encoding='utf-8') as f:
                    raw = yaml.safe_load(f) or []
                for e in raw:
                    if isinstance(e, dict):
                        tgt = (e.get('target_term') or e.get('target') or '').strip()
                        if tgt:
                            targets.add(tgt)
            except Exception:
                continue
        return targets


def write_learned_glossary(candidates: List[dict], project_name: str) -> str:
    """Ghi candidates ra glossary_<film>.learned.yaml. Trả về path."""
    safe = re.sub(r'[^\w\-_]', '_', project_name or 'unknown')
    path = f'glossary_{safe}.learned.yaml'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'# glossary_{safe}.learned.yaml\n')
        f.write('# TU HOC TUONG DOI — file do pipeline sinh ra, KHONG sua tay.\n')
        f.write('# De dung: copy entry vao glossary_uncen.yaml (nhung chung) hoac\n')
        f.write('# glossary_<film>.yaml (rieng phim).\n')
        f.write('# Chi lay nhung term confidence >= 0.9 va freq >= 3.\n\n')
        if not candidates:
            f.write('[]\n')
        for c in candidates:
            f.write(f"- source_term: {c['source_term']}\n")
            f.write(f"  target_term: {c['target_term']}\n")
            f.write(
                f"  context_hint: auto-learned (freq={c['freq']}, "
                f"confidence={c['confidence']})\n\n"
            )
    return path


def write_global_learned_glossary(candidates: List[dict], path: str = 'glossary_learned.yaml') -> str:
    """Ghi các term đã kiểm chứng qua nhiều project ra glossary_learned.yaml."""
    filtered = [
        c for c in candidates
        if c.get('confidence', 0) >= 0.75
        and (c.get('project_count', 1) >= 2 or c.get('freq', 0) >= 5)
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# glossary_learned.yaml\n')
        f.write('# Tu vung uncen tu hoc xuyen suot nhieu du an (auto-generated).\n\n')
        if not filtered:
            f.write('[]\n')
        for c in filtered:
            f.write(f"- source_term: {c['source_term']}\n")
            f.write(f"  target_term: {c['target_term']}\n")
            f.write(
                f"  context_hint: auto-learned (freq={c['freq']}, "
                f"films={c.get('project_count', 1)}, confidence={c['confidence']})\n\n"
            )
    return path


def main(argv=None):
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    import yaml
    from core.database import Database

    with open(os.path.join(root, 'config.yaml'), encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}

    db = Database()
    miner = GlossaryMiner(db, cfg)

    if argv[0] == '--all':
        cands = miner.mine_all_projects(skip_existing=True)
        print(f"[MINER] {len(cands)} cross-project candidate(s)")
        for c in cands:
            print(f"  {c['source_term']!r:20} -> {c['target_term']!r:25} "
                  f"(freq={c['freq']}, films={c.get('project_count', 1)}, conf={c['confidence']})")
        path = write_global_learned_glossary(cands)
        print(f"[MINER] Da ghi {path}")
        return 0

    project = db.get_project_by_name(argv[0])
    if not project:
        print(f"[!] Khong tim thay project '{argv[0]}'")
        return 1

    cands = miner.mine(project['id'])
    print(f"[MINER] {len(cands)} candidate(s) tu project {argv[0]}")
    for c in cands:
        print(f"  {c['source_term']!r:20} -> {c['target_term']!r:25} "
              f"(freq={c['freq']}, conf={c['confidence']})")

    if cands:
        path = write_learned_glossary(cands, project['name'])
        print(f"[MINER] Da ghi {path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
