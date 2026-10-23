# -*- coding: utf-8 -*-
"""Test cac sua doi cua gui.py/main.py: glossary file-based, window preset, non-interactive."""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import yaml  # noqa: E402
import main as M  # noqa: E402
import gui as G  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_PY = os.path.join(ROOT, 'gui.py')
MAIN_PY = os.path.join(ROOT, 'main.py')
FAIL = []


def check(cond, msg):
    print(('  PASS ' if cond else '  FAIL ') + msg)
    if not cond:
        FAIL.append(msg)


# ---------------------------------------------------------------- 1. glossary file
print('=== 1. Glossary ghi/ doc file YAML ===')
tmp = tempfile.mkdtemp()
cwd = os.getcwd()
os.chdir(tmp)

G.save_glossary_terms('JUL-837', [
    {'source_term': 'コスケ', 'target_term': 'Kousuke'},
    {'source_term': 'セフレ', 'target_term': 'người tình', 'context_hint': 'bạn tình'},
    {'source_term': '', 'target_term': 'bo qua'},
    {'source_term': 'bo qua', 'target_term': ''},
])
path = os.path.join(tmp, 'glossary_JUL-837.yaml')
check(os.path.exists(path), 'tao duoc glossary_JUL-837.yaml')
data = yaml.safe_load(io.open(path, encoding='utf-8').read())
check(len(data) == 2, 'chi giu 2 term hop le (bo 2 dong rong), thuc te %d' % len(data))
check(data[0] == {'source_term': 'コスケ', 'target_term': 'Kousuke'},
      'dung schema source_term/target_term')
check(data[1].get('context_hint') == 'bạn tình', 'giu context_hint, thuc te %r' % data[1].get('context_hint'))

# Doc nguoc len
os.remove(path)
io.open(path, 'w', encoding='utf-8').write(
    yaml.safe_dump([{'source_term': 'ア', 'target_term': 'A'}], allow_unicode=True))
check(G.glossary_file_for('JUL-837') == 'glossary_JUL-837.yaml', 'ten file dung chuan pipeline')
check(G.glossary_file_for('JUL 837/x') == 'glossary_JUL_837_x.yaml',
      'ky tu la duoc sanitize y het pipeline')

# Test pipeline doc file vua tao (integration)
from core.pipeline import TranslationPipeline  # noqa: E402
p = TranslationPipeline.__new__(TranslationPipeline)
terms = p._load_glossary_terms({'project': {'name': 'JUL-837'},
                               'translation': {'mode': 'uncen'}})
check(any(t['source_term'] == 'ア' for t in terms),
      'PIPELINE DOC DUOC file GUI vua tao (%d term)' % len(terms))

# Thuoc tinh doc cua GUI phai khop pipeline
check(G.load_glossary_files('JUL-837') == terms,
      'GUI doc cung ket qua voi pipeline')

# ---------------------------------------------------------------- 2. window preset
print('\n=== 2. Window preset khop main.py ===')

src = io.open(GUI_PY, encoding='utf-8').read()
check('from main import WINDOW_PRESETS' in src,
      'GUI import WINDOW_PRESETS tu main.py (1 nguon su that, khong nhan doi ban sao)')
check(src.count('WINDOW_PRESETS = {') == 0, 'GUI khong nhan doi bang WINDOW_PRESETS rieng')

# Hanh dong giong main.py
cfg = {'project': {'source_lang': 'Korean'}, 'window': {'size': 6, 'history': 12, 'future': 4}}
M._apply_window_preset(cfg, 'ko')
expect = {'size': 8, 'history': 8, 'future': 2}
check(cfg['window'] == expect, 'main.py _apply_window_preset(ko) -> %s' % expect)

# ---------------------------------------------------------------- 3. non-interactive
print('\n=== 3. --non-interactive ===')
check('--non-interactive' in src, 'GUI truyen --non-interactive')
import inspect  # noqa: E402
sig = inspect.signature(M.run_translation)
check('non_interactive' in sig.parameters, 'run_translation nhan tham so non_interactive')
check(sig.parameters['non_interactive'].default is False,
      'default False (interactive mode giu hoi nhu cu)')

mainsrc = io.open(MAIN_PY, encoding='utf-8').read()
import re  # noqa: E402
fn_start = mainsrc.index('def run_translation')
fn_end = mainsrc.index('\ndef ', fn_start + 10)
body = mainsrc[fn_start:fn_end]
# Bo docstring (chi doc ma chay thuc) roi dem input( co dau ngoac
code = re.sub(r'"""[\s\S]*?"""', '', body)
inputs = list(re.finditer(r'(?<![\w.])input\(', code))
check(len(inputs) == 2, 'run_translation goi input() dung 2 lan, thuc te %d' % len(inputs))
for m in inputs:
    before = code[max(0, m.start() - 300):m.start()]
    guarded = ('non_interactive' in before)
    check(guarded, 'input() #%d co guard non_interactive truoc do' % (inputs.index(m) + 1))

# ---------------------------------------------------------------- 4. khong con config['glossary']
print('\n=== 4. GUI khong con doc/ghi config["glossary"] ===')
check('self.app.config["glossary"] = new_gloss' not in src,
      'khong con gan config["glossary"] trong _save')
check('self.config.get("glossary", [])' not in src,
      'khong con doc config["glossary"] de nap UI')

# ---------------------------------------------------------------- 5. model defaults
print('\n=== 5. Default LLM lay tu main.py, khong hardcode trong GUI ===')
check('get_active_model_config' in src,
      'GUI goi get_active_model_config cua main.py thay vi hardcode default')

# moi truong hop resolve cua main.py -> GUI phai tra ve dung y
CASES = [
    ('uncen co models.uncen', {'translation': {'mode': 'uncen'},
                               'project': {'source_lang': 'Japanese'},
                               'models': {'uncen': {'name': 'X'}}}),
    ('default mode, source ja', {'translation': {'mode': 'default'},
                                 'project': {'source_lang': 'Japanese'},
                                 'models': {'default': {'name': 'D'},
                                            'ja': {'name': 'J'}}}),
    ('default mode, khong co models', {'translation': {'mode': 'default'},
                                       'project': {'source_lang': 'Korean'},
                                       'models': {}}),
    ('uncen, khong co models.uncen', {'translation': {'mode': 'uncen'},
                                      'project': {'source_lang': 'Japanese'},
                                      'models': {}}),
]
KEYS = ['num_predict', 'num_ctx', 'repeat_penalty', 'temperature', 'timeout', 'ollama_url', 'name']
for label, cfg in CASES:
    a = M.get_active_model_config(dict(cfg))
    b = G.resolved_model_config(dict(cfg))
    check(all(a[k] == b[k] for k in KEYS),
          '%s: num_predict=%s num_ctx=%s rp=%s temp=%s' % (
              label, a['num_predict'], a['num_ctx'], a['repeat_penalty'], a['temperature']))

# GUI phai hien thi + luu duoc num_ctx va repeat_penalty (2 field con thieu)
check('num_ctx_edit' in src, 'GUI co o nhap num_ctx')
check('repeat_penalty_edit' in src, 'GUI co o nhap repeat_penalty')
check('llm_cfg["num_ctx"]' in src, 'GUI luu num_ctx vao models.{mode}')
check('llm_cfg["repeat_penalty"]' in src, 'GUI luu repeat_penalty vao models.{mode}')
check('active["num_ctx"]' in src and 'active["repeat_penalty"]' in src,
      'GUI nap gia tri num_ctx/repeat_penalty tu main.py resolve')

# Khong con default so hardcode trong phan LLM cua gui.py
llm_block = src[src.index('llm_cfg["num_predict"]') - 800: src.index('llm_cfg["ollama_url"]')]
hard = re.findall(r'=\s*(20\d\d|10\d\d|61\d\d|0\.0\d|1\.1\d|1\.2\d|180|120)\b', llm_block)
check(not hard, 'khong con default so hardcode trong khoi LLM, thuc te %s' % hard)

# ---------------------------------------------------------------- 6. GUI doc config that
print('\n=== 6. GUI hien thi dung gia tri config dang luu (khong sua config) ===')
real_cfg = yaml.safe_load(io.open(os.path.join(ROOT, 'config.yaml'), encoding='utf-8'))
check('model' in real_cfg, 'block model top-level van con (main.py tu ghi de moi lan chay)')

# GUI phai hien thi DUNG gia tri trong config, khong phai default cua code.
# config cua anh dat num_predict: 1024 -> GUI phai hien thi 1024.
active = G.resolved_model_config(real_cfg)
cfg_val = real_cfg['models'][real_cfg['translation']['mode']].get('num_predict')
check(active['num_predict'] == cfg_val,
      'GUI hien thi num_predict=%d = gia tri trong config (%d)' % (active['num_predict'], cfg_val))

# va config van phai la nguon su that khi co gia tri ghi ro
check(active['timeout'] == real_cfg['models']['uncen']['timeout'],
      'GUI ton trong timeout=300 cua anh (khong reset ve default 180)')

check(M.validate_config(real_cfg) == [],
      'config.yaml van hop le theo validate_config cua main.py')

os.chdir(cwd)

print('\n=== KET QUA ===')
if FAIL:
    print('FAIL %d:' % len(FAIL))
    for f in FAIL:
        print('  -', f)
    sys.exit(1)
print('TAT CA PASS')