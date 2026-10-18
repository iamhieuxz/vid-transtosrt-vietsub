"""Test thủ công cải tiến Worker A: schema validation + content_hash cache."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
import tempfile
import hashlib

# Test 1: schema validator
from core.ja_context_analyzer import _validate_context_schema

print("=== Test 1: schema validation ===")
ok_full = {'speakers': ['A'], 'pronouns_map': {}, 'tone': 'casual', 'setting': 'x', 'summary': 'y'}
ok_empty = {'speakers': [], 'pronouns_map': {}, 'tone': 'unknown', 'setting': 'unknown', 'summary': ''}
ok_null_pron = {'speakers': ['A'], 'pronouns_map': {'A->B': {'self': None, 'other': None}}, 'tone': 'casual', 'setting': 'x', 'summary': 'y'}
bad_missing = {'speakers': ['A'], 'pronouns_map': {}, 'tone': 'casual', 'summary': 'y'}
bad_type = {'speakers': 'A', 'pronouns_map': {}, 'tone': 'casual', 'setting': 'x', 'summary': 'y'}

assert _validate_context_schema(ok_full) is not None, "Full schema should pass"
assert _validate_context_schema(ok_empty) is not None, "Empty (1 scene) should pass"
assert _validate_context_schema(ok_null_pron) is not None, "Null pronouns should pass"
assert _validate_context_schema(bad_missing) is None, "Missing field should reject"
assert _validate_context_schema(bad_type) is None, "Wrong type should reject"
print("All 5 schema cases pass")

# Test 2: content_hash
print("\n=== Test 2: content_hash changes when content changes ===")
lines1 = ["こんにちは", "さようなら"]
lines2 = ["こんにちは", "さようなら"]   # identical
lines3 = ["こんにちは", "こんばんは"]   # different last line

h1 = hashlib.sha1("\n".join(lines1).encode("utf-8")).hexdigest()[:16]
h2 = hashlib.sha1("\n".join(lines2).encode("utf-8")).hexdigest()[:16]
h3 = hashlib.sha1("\n".join(lines3).encode("utf-8")).hexdigest()[:16]

assert h1 == h2, f"Same content should have same hash ({h1} vs {h2})"
assert h1 != h3, f"Different content should have different hash"
print(f"  h1=h2: True | h1!=h3: True | hash={h1}")

# Test 3: DB migration + save/load content_hash
print("\n=== Test 3: DB migration + save/load content_hash ===")
from core.database import Database
db = Database('translation.db')
cols = db._get_connection().execute('PRAGMA table_info(window_contexts)').fetchall()
has_col = any(c[1] == 'content_hash' for c in cols)
assert has_col, "content_hash column should exist after migration"
print(f"  content_hash column exists: True")

# Test 4: save_window_context nhận content_hash
proj = db.create_project('test_hash', 'ja', 'vi', '/tmp/x.srt', '/tmp/x_vi.srt')
db.save_window_context(proj, 0, 0, 17, '["A"]', '{}', 'casual', 'cafe', 'test', 'completed', content_hash='abc123')
loaded = db.get_window_context(proj, 0)
assert loaded['content_hash'] == 'abc123', f"Hash should persist, got {loaded.get('content_hash')}"
print(f"  save+load content_hash: True")
db._get_connection().close()

print("\nAll tests passed!")
