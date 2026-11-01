from compare_srt import compare


def _srt(path, lines):
    body = "\n".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{t}\n" for i, t in enumerate(lines, 1))
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_detects_regression_and_fix(tmp_path):
    src = _srt(tmp_path / "s.srt", ["ここに来ないでください", "兄さんが好きです", "ありがとうございます"])
    a = _srt(tmp_path / "a.srt", ["Hãy đến đây", "Tôi thích cậu", "Cảm ơn rất nhiều"])
    b = _srt(tmp_path / "b.srt", ["Đừng đến đây", "Tôi thích anh", "Cảm ơn rất nhiều ありがとう"])
    r = compare(src, a, b)
    assert r["fixed"] == [1]        # phủ định được sửa
    assert r["regressed"] == [3]    # rò rỉ kana mới
    assert r["changed"] == 3
    assert r["a"]["pronouns"]["cậu"] == 1 and r["b"]["pronouns"]["anh"] == 1
