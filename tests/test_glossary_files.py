"""Test _load_glossary_terms — nạp glossary theo mode, ưu tiên file.

Cấu trúc sau khi tách:
  glossary_uncen.yaml    (chỉ khi mode uncen)
  glossary_common.yaml
  glossary_<project>.yaml (ghi đè common cùng source_term)
"""
import io
import os
import sys
import shutil
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import TranslationPipeline


def load(config, files):
    """Load glossary trong thư mục tạm chứa `files` (dict name -> content)."""
    tmp = tempfile.mkdtemp(prefix='gloss_')
    try:
        for name, content in files.items():
            with open(os.path.join(tmp, name), 'w', encoding='utf-8') as f:
                f.write(content)
        p = TranslationPipeline.__new__(TranslationPipeline)
        old = os.getcwd()
        os.chdir(tmp)
        try:
            return TranslationPipeline._load_glossary_terms(p, config)
        finally:
            os.chdir(old)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


UNCEN_FILE = '- source_term: ちんこ\n  target_term: dương vật\n'
COMMON_FILE = '- source_term: ほら\n  target_term: Nhìn này\n'


def test_uncen_mode_loads_uncen_file():
    terms = load(
        {'project': {'name': 'X'}, 'translation': {'mode': 'uncen'}},
        {'glossary_uncen.yaml': UNCEN_FILE, 'glossary_common.yaml': COMMON_FILE})
    srcs = {t['source_term'] for t in terms}
    assert srcs == {'ちんこ', 'ほら'}, srcs


def test_default_mode_skips_uncen_file():
    terms = load(
        {'project': {'name': 'X'}, 'translation': {'mode': 'default'}},
        {'glossary_uncen.yaml': UNCEN_FILE, 'glossary_common.yaml': COMMON_FILE})
    srcs = {t['source_term'] for t in terms}
    assert srcs == {'ほら'}, srcs


def test_per_film_overrides_common():
    terms = load(
        {'project': {'name': 'XYZ'}, 'translation': {'mode': 'default'}},
        {'glossary_common.yaml': '- source_term: 主人\n  target_term: chồng\n',
         'glossary_XYZ.yaml': '- source_term: 主人\n  target_term: chồng của em\n'})
    assert len(terms) == 1, terms
    assert terms[0]['target_term'] == 'chồng của em', terms


def test_falls_back_to_config_glossary():
    """Không có file nào → dùng config.yaml['glossary']."""
    terms = load(
        {'project': {'name': 'NOPE'}, 'translation': {'mode': 'uncen'},
         'glossary': [{'source': 'あ', 'target': 'b', 'context': 'c'}]},
        {})
    assert len(terms) == 1, terms
    assert terms[0]['source_term'] == 'あ', terms
    assert terms[0]['target_term'] == 'b', terms


def test_missing_files_do_not_crash():
    terms = load(
        {'project': {'name': 'NOPE'}, 'translation': {'mode': 'uncen'}}, {})
    assert terms == [], terms


if __name__ == '__main__':
    tests = [
        test_uncen_mode_loads_uncen_file,
        test_default_mode_skips_uncen_file,
        test_per_film_overrides_common,
        test_falls_back_to_config_glossary,
        test_missing_files_do_not_crash,
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