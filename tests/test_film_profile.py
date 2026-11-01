"""film_profile: tên chỉ được xác nhận khi có bằng chứng trong dòng JA; tóm tắt/vai lẫn chữ Nhật bị loại."""
import json

from core.database import Database
from core.film_profile import (_validate_profile, build_film_profile, confirm_names,
                               render_profile_block, write_characters_glossary)

MEI = {"canonical": "Mei", "aliases": ["メイ", "メイさん", "Mei"]}
MIMI = {"canonical": "Mimi", "aliases": ["ミミ", "Mimi"]}


def _w(speakers):
    return {"speakers_json": json.dumps(speakers, ensure_ascii=False)}


def test_confirm_requires_listed_and_present_in_ja_text_in_enough_windows():
    ws = [_w(["Mei-san"]), _w(["メイ"]), _w(["Mei", "Mimi"]), _w(["Mimi"])]
    texts = ["メイさん、こんにちは", "メイ！", "ありがとう", "ミミ？"]
    # Mei: 2 cửa sổ hợp lệ (cửa sổ 3 được nhắc nhưng JA không có メイ); Mimi: 1
    assert confirm_names(ws, texts, [MEI, MIMI], min_windows=2) == [
        {"canonical": "Mei", "aliases": MEI["aliases"], "windows": 2}]
    assert confirm_names(ws, texts, [MEI, MIMI], min_windows=3) == []


def test_validate_profile_rejects_japanese_and_unknown_names():
    good = {"genre": "drama", "setting": "nhà", "synopsis": "x", "roles": {"Mei-san": "chị dâu", "Reisa": "bịa"}}
    assert _validate_profile(good, ["Mei"])["roles"] == {"Mei": "chị dâu"}
    assert _validate_profile(dict(good, synopsis="メイが"), ["Mei"]) is None
    assert _validate_profile({"genre": "", "setting": "", "synopsis": "", "roles": {}}, ["Mei"]) is None
    # synopsis nhắc tên chưa xác nhận (Mimi) -> loại
    assert _validate_profile(dict(good, synopsis="Mei và Mimi ở nhà."), ["Mei"], ["Mimi"]) is None
    assert _validate_profile(good, ["Mei"], ["Mimi"]) is not None


def test_build_profile_caches_and_writes_pending_glossary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = Database(str(tmp_path / "t.db"))
    pid = db.create_project("T", "ja", "vi", "a.srt", "b.srt")
    with db._get_connection() as c:
        for i in range(6):
            c.execute("INSERT INTO subtitle_items (project_id, sub_index, start_time, end_time, original_text, status) "
                      "VALUES (?,?,?,?,?,?)", (pid, i + 1, "0", "1", "メイさん、どうぞ", "pending"))
        c.commit()
    for idx in range(2):
        db.save_window_context(pid, idx, idx * 3, idx * 3 + 2, json.dumps(["Mei"]), "{}", "casual", "nhà", "Mei ở nhà.")

    class T:
        calls = 0

        def generate(self, prompt, temperature=None, _retries=None):
            T.calls += 1
            return json.dumps({"genre": "drama", "setting": "nhà", "synopsis": "Mei ở nhà.", "roles": {"Mei": "chị dâu"}})

    p = build_film_profile(db, pid, T(), [MEI, MIMI], min_windows=2)
    assert [n["canonical"] for n in p["names"]] == ["Mei"] and p["roles"] == {"Mei": "chị dâu"}
    build_film_profile(db, pid, T(), [MEI, MIMI], min_windows=2)
    assert T.calls == 1  # lần 2 trúng cache theo source_hash
    assert "Thể loại: drama" in render_profile_block(p)
    path = write_characters_glossary(p, "T")
    text = open(path, encoding="utf-8").read()
    assert "source_term: メイ" in text and "chị dâu" in text and "Mimi" not in text
    # file đã có (người dùng duyệt tay) -> không ghi đè, xuất .new.yaml
    open(path, "w", encoding="utf-8").write("# edited\n")
    assert write_characters_glossary(p, "T").endswith(".characters.new.yaml")
    assert open(path, encoding="utf-8").read() == "# edited\n"
