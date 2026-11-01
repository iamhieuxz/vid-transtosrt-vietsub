"""So sánh tự động 2 bản dịch SRT của cùng một file Whisper (JA -> VI).

    python compare_srt.py SRC_JA.srt OLD_VI.srt NEW_VI.srt

Chỉ dùng luật có sẵn của JaQAChecker (rò rỉ CJK, phủ định, độ dài) cộng đếm
xưng hô và số dòng đổi. Đây là tín hiệu hồi quy, KHÔNG thay cho đọc mắt:
shortcut: không đo nghĩa, chỉ bắt lỗi cơ học; cần LLM-judge/người khi điểm bằng nhau.
"""
import re
import sys
from collections import Counter

import pysrt

from core.ja_qa_checker import JaQAChecker

PRONOUNS = ("cậu", "tớ", "anh", "chị", "em", "tôi", "mày", "tao")


def _texts(path):
    return [s.text.replace("\n", " ").strip() for s in pysrt.open(path, encoding="utf-8")]


def score(src, vi):
    qa = JaQAChecker()
    issues = Counter()
    bad = {}
    for i, (ja, v) in enumerate(zip(src, vi), 1):
        r = qa.inspect_line(ja, v)
        for k in r["issues"]:
            issues[k] += 1
        if r["issues"]:
            bad[i] = r["issues"]
    pron = Counter()
    for v in vi:
        low = v.lower()
        for p in PRONOUNS:
            if re.search(rf"\b{p}\b", low):
                pron[p] += 1
    return {"lines": len(vi), "issues": issues, "pronouns": pron, "bad": bad}


def compare(src_path, a_path, b_path):
    src, a, b = _texts(src_path), _texts(a_path), _texts(b_path)
    if not (len(src) == len(a) == len(b)):
        raise ValueError(f"So dong khac nhau: src={len(src)} a={len(a)} b={len(b)}")
    sa, sb = score(src, a), score(src, b)
    changed = sum(1 for x, y in zip(a, b) if x != y)
    regress = sorted(set(sb["bad"]) - set(sa["bad"]))
    fixed = sorted(set(sa["bad"]) - set(sb["bad"]))
    return {"a": sa, "b": sb, "changed": changed, "regressed": regress, "fixed": fixed}


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    r = compare(*argv[1:])
    for name in ("a", "b"):
        s = r[name]
        print(f"[{name.upper()}] dong={s['lines']} loi={sum(s['issues'].values())} {dict(s['issues'])}")
        print(f"      xung ho: {dict(s['pronouns'])}")
    print(f"Doi {r['changed']} dong | B moi loi: {len(r['regressed'])} {r['regressed'][:15]} "
          f"| B het loi: {len(r['fixed'])} {r['fixed'][:15]}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
