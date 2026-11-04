# -*- coding: utf-8 -*-
"""Per-film fix packs — tách data sửa lỗi của từng bộ phim ra khỏi code.

Vì sao: mỗi phim mới trước đây phải thêm entry hardcode vào
``whisper_normalizer._WHISPER_HOMOPHONE_FIXES`` / ``post_processor._SOURCE_CONDITIONED_FIXES``,
khiến các thay thế toàn cục (vd ``仲立ち -> 中出し``) bôi lỗi sang phim khác.

Cơ chế: file ``film_fixes_<PROJECT>.yaml`` ở thư mục gốc project.
Chỉ được nạp khi tên project khớp — phim khác không bị ảnh hưởng.

Schema::

    homophone:            # sửa Whisper nghe nhầm (chuỗi -> chuỗi)
      - from: 仲立ち
        to: 中出し
    mora_clip:            # khôi phục âm tiết đầu câu bị VAD cắt cụt (regex)
      - pattern: '^\\s*さんのまんこ'
        replace: お母さんのまんこ
    source_conditioned:   # sửa bản dịch khi câu JA gốc khớp (regex nguồn, regex sai, chuỗi đúng)
      - source: 娘を身ごもっ
        target: '\\bcô ấy mang thai\\b'
        replace: tôi mang thai con gái
"""
from __future__ import annotations

import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)


def film_fixes_path(project_name: str) -> str:
    """Đường dẫn file fix của phim ('' nếu tên không hợp lệ)."""
    safe = re.sub(r'[^\w\-_]', '_', project_name or '')
    return f'film_fixes_{safe}.yaml' if safe else ''


def _empty() -> dict:
    return {'homophone': [], 'mora_clip': [], 'source_conditioned': []}


def load_film_fixes(project_name: str) -> dict:
    """Load + compile fix pack cho 1 phim. Thiếu file/lỗi -> pack rỗng (non-blocking)."""
    path = film_fixes_path(project_name)
    if not path or not os.path.exists(path):
        return _empty()

    try:
        with open(path, encoding='utf-8') as f:
            raw = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"film_fixes: doc loi {path}: {e}")
        return _empty()

    homophone = []
    for item in raw.get('homophone') or []:
        if isinstance(item, dict) and item.get('from') and item.get('to') is not None:
            homophone.append((str(item['from']), str(item['to'])))

    mora_clip = []
    for item in raw.get('mora_clip') or []:
        try:
            mora_clip.append((re.compile(str(item['pattern'])), str(item.get('replace', ''))))
        except (KeyError, re.error) as e:
            logger.warning(f"film_fixes: bo qua mora_clip loi trong {path}: {e}")

    source_conditioned = []
    for item in raw.get('source_conditioned') or []:
        try:
            src_re = re.compile(str(item['source']), re.IGNORECASE)
            tgt_re = re.compile(str(item['target']), re.IGNORECASE)
            rep = str(item.get('replace', ''))
            if rep:
                source_conditioned.append((src_re, tgt_re, rep))
        except (KeyError, re.error) as e:
            logger.warning(f"film_fixes: bo qua source_conditioned loi trong {path}: {e}")

    logger.info(
        f"film_fixes: {project_name}: homophone={len(homophone)}, "
        f"mora_clip={len(mora_clip)}, source_conditioned={len(source_conditioned)}"
    )
    return {
        'homophone': homophone,
        'mora_clip': mora_clip,
        'source_conditioned': source_conditioned,
    }
