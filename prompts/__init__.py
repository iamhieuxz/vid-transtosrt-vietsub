"""Prompt loader for language-pair translation prompts.

Each language pair has its own YAML file under prompts/<src>-<tgt>.yaml.
Common chunks live in prompts/_base.yaml and are included via the
``{include <name>}`` placeholder.

The loader resolves include placeholders recursively, then exposes the
final templates via a small ``PromptSet`` dataclass.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_FILE = os.path.join(PROMPTS_DIR, "_base.yaml")
DEFAULT_BASE_NAME = "_base"


@dataclass
class PromptSet:
    """Resolved prompt templates for one language pair."""

    pair: str
    source_lang_full: str
    target_lang_full: str
    target_output_lang: str
    description: str
    passes: List[str] = field(default_factory=list)
    sections: Dict[str, str] = field(default_factory=dict)

    def render(self, section: str, **vars) -> str:
        """Render a section, substituting ``{var}`` placeholders."""
        template = self.sections.get(section, "")
        for key, value in vars.items():
            template = template.replace("{" + key + "}", str(value))
        return template.strip()

    def has(self, section: str) -> bool:
        return section in self.sections


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Prompt file {path} must be a YAML mapping at top level")
    return data


def _resolve_includes(text: str, base: Dict[str, Any], seen: Optional[set] = None) -> str:
    """Replace ``{include <name>}`` with ``base[name]`` recursively."""
    if seen is None:
        seen = set()

    include_re = re.compile(r"\{include\s+([\w\-]+)\s*\}")

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name in seen:
            return f"{{include {name}}}"
        if name not in base:
            raise KeyError(f"Included prompt section '{name}' not found in _base.yaml")
        seen.add(name)
        return _resolve_includes(str(base[name]), base, seen)

    prev = None
    while prev != text:
        prev = text
        text = include_re.sub(_replace, text)
    return text


class PromptRegistry:
    """Lazy cache of loaded language-pair prompt sets."""

    def __init__(self, prompts_dir: str = PROMPTS_DIR):
        self._dir = prompts_dir
        self._cache: Dict[str, PromptSet] = {}
        self._base: Optional[Dict[str, Any]] = None

    def _load_base(self) -> Dict[str, Any]:
        if self._base is None:
            self._base = _load_yaml(BASE_FILE)
        return self._base

    def _path_for(self, pair: str) -> str:
        safe = pair.replace("/", "_").replace("\\", "_")
        return os.path.join(self._dir, f"{safe}.yaml")

    def get(self, pair: str) -> PromptSet:
        if pair in self._cache:
            return self._cache[pair]

        path = self._path_for(pair)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No prompt file for language pair '{pair}' (expected: {path})"
            )

        data = _load_yaml(path)
        meta = data.get("meta") or {}
        base = self._load_base()

        # Treat 'meta' and all other top-level keys except reserved ones as
        # prompt sections. We exclude 'meta' and 'passes' (list) from sections.
        reserved = {"meta"}
        sections: Dict[str, str] = {}
        for key, value in data.items():
            if key in reserved:
                continue
            if not isinstance(value, str):
                # e.g. 'passes' (list) is consumed via meta below
                continue
            sections[key] = _resolve_includes(value, base)

        prompt_set = PromptSet(
            pair=meta.get("pair", pair),
            source_lang_full=meta.get("source_lang_full", ""),
            target_lang_full=meta.get("target_lang_full", ""),
            target_output_lang=meta.get("target_output_lang", "vi"),
            description=meta.get("description", ""),
            passes=list(meta.get("passes") or []),
            sections=sections,
        )
        self._cache[pair] = prompt_set
        return prompt_set

    def available(self) -> List[str]:
        pairs: List[str] = []
        for name in os.listdir(self._dir):
            if name.endswith(".yaml") and name != "_base.yaml":
                stem = name[:-5]
                if stem == DEFAULT_BASE_NAME:
                    continue
                pairs.append(stem)
        return sorted(pairs)


# Module-level singleton — keep it simple, no global state besides cache.
registry = PromptRegistry()
