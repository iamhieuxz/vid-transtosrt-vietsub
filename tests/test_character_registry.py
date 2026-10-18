"""Test CharacterRegistry."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile
import json
import sqlite3
from unittest.mock import MagicMock
from core.character_registry import CharacterRegistry


def make_mock_db(speakers_by_window):
    """Mock DB trả list_context_windows với speakers đã fake."""
    db = MagicMock()
    contexts = []
    for idx, speakers in enumerate(speakers_by_window):
        contexts.append({
            "context_window_index": idx,
            "status": "completed",
            "speakers_json": json.dumps(speakers, ensure_ascii=False),
        })
    db.list_context_windows.return_value = contexts
    return db


def test_registry_basic():
    """Build registry từ mock data — should pick canonical cho Ayumi, Takumi, Kawaguchi."""
    db = make_mock_db([
        ["あゆみ", "川口くん", "たくみ"],   # window 0
        ["あゆみ", "さゆ", "たくみ"],        # window 1
    ])
    reg = CharacterRegistry(db, project_id=1)
    mapping = reg.build()

    # あゆみ → Ayumi (theo config)
    assert mapping.get("あゆみ") == "Ayumi", f"あゆみ: {mapping.get('あゆみ')}"
    # たくみ → Takumi (theo config vừa thêm)
    assert mapping.get("たくみ") == "Takumi", f"たくみ: {mapping.get('たくみ')}"
    # 川口くん, さゆ không có trong config → giữ nguyên
    assert mapping.get("川口くん") == "川口くん"
    assert mapping.get("さゆ") == "さゆ"
    print("test_registry_basic: PASS")


def test_registry_canonical_priority():
    """Cùng tên có nhiều alias — ưu tiên canonical từ config."""
    db = make_mock_db([
        ["あゆみ", "アユミ", "Ayumi", "川口くん"],
    ])
    reg = CharacterRegistry(db, project_id=1)
    mapping = reg.build()
    # Cả 3 dạng của Ayumi → cùng canonical "Ayumi"
    assert mapping["あゆみ"] == "Ayumi"
    assert mapping["アユミ"] == "Ayumi"
    assert mapping["Ayumi"] == "Ayumi"
    print("test_registry_canonical_priority: PASS")


def test_validate_translation():
    """Validate fix lỗi dịch tên sai: source='あゆみ' target='Aya' → suggest='Ayumi'."""
    db = make_mock_db([
        ["あゆみ", "川口くん"],
    ])
    reg = CharacterRegistry(db, project_id=1)
    reg.build()

    # Test 1: target có "Aya" sai, không có "Ayumi" → suggest fix
    src = "あゆみさんのことが好きなんです"
    tgt = "Em thích Aya-san lắm."
    fixed = reg.validate_translation(src, tgt)
    assert fixed is not None, "Should detect Aya as wrong form"
    assert "Ayumi" in fixed, f"Fixed should contain 'Ayumi': {fixed!r}"
    assert "Aya" not in fixed or "Ayumi" in fixed, f"Fixed still has 'Aya': {fixed!r}"
    print(f"  Fixed: {fixed!r}")

    # Test 2: target có "Ayumi" đúng → None
    tgt_ok = "Em thích Ayumi-san lắm."
    fixed2 = reg.validate_translation(src, tgt_ok)
    assert fixed2 is None, f"OK translation should not be flagged: {fixed2!r}"
    print("test_validate_translation: PASS")


def test_registry_pure_japanese_no_canonical():
    """Tên thuần JA không có trong config → giữ nguyên (warning log)."""
    db = make_mock_db([
        ["太郎"],   # Tarou - not in config
    ])
    reg = CharacterRegistry(db, project_id=1)
    mapping = reg.build()
    assert mapping.get("太郎") == "太郎", f"Pure JA should keep: {mapping.get('太郎')}"
    print("test_registry_pure_japanese_no_canonical: PASS")


if __name__ == '__main__':
    test_registry_basic()
    test_registry_canonical_priority()
    test_validate_translation()
    test_registry_pure_japanese_no_canonical()
    print("\nAll CharacterRegistry tests passed!")
