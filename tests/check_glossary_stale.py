# -*- coding: utf-8 -*-
"""Check glossary_<project>.yaml con dung voi file whisper hien tai khong.

Ly do can: Whisper doi kana/kanji giua cac lan chay. JUL-837 lan 01:03
Whisper viet "ミナミさん"/"光介" trong khi glossary viet "みなみ"/"こうすke"
tu ban Whisper luc 00:01 → khong term nao match → LLM tu dich ten →
"Kouzuke"/"Mina mi".

Dung:  python tests/check_glossary_stale.py E:/HIDE/JAV/JUL-837
Exit 1 neu co source_term khong con xuat hien trong whisper SRT.
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import yaml

JA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]')


def load_srt_text(path):
    raw = io.open(path, encoding='utf-8-sig').read()
    out = []
    for b in raw.strip().split('\n\n'):
        L = [x for x in b.strip().split('\n') if x.strip()]
        if len(L) >= 3 and '-->' in L[1]:
            out.append(L[2])
    return '\n'.join(out)


def main(folder, glossary_path):
    project = os.path.basename(folder.rstrip('\\/'))
    whisper = os.path.join(folder, '%s_whisper.srt' % project)
    if not os.path.exists(whisper):
        print('[SKIP] khong thay %s' % whisper)
        return 0

    text = load_srt_text(whisper)
    data = yaml.safe_load(io.open(glossary_path, encoding='utf-8').read())
    stale, unused = [], []
    for e in data:
        term = e['source_term'].strip()
        if not term:
            continue
        if term not in text:
            stale.append(term)

    if stale:
        print('[FAIL] %s: %d/%d source_term KHONG con trong %s'
              % (glossary_path, len(stale), len(data), os.path.basename(whisper)))
        print('       nen xac nhung: %s' % ', '.join(stale))
        print('       -> Whisper da doi kana/kanji. Cap nhat glossary theo file moi.')
        return 1
    print('[ OK ] %s: tat ca %d source_term deu ton tai trong %s'
          % (glossary_path, len(data), os.path.basename(whisper)))
    return 0


if __name__ == '__main__':
    folder = sys.argv[1] if len(sys.argv) > 1 else r'E:\HIDE\JAV\JUL-837'
    project = os.path.basename(folder.rstrip('\\/'))
    gloss = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '..', 'glossary_%s.yaml' % project)
    sys.exit(main(folder, gloss))