"""Lightweight config schema validation — fail fast on malformed config.

Replaces ad-hoc validate_config() in main.py with structured error reporting.
Each validator returns a list of error strings (empty = OK).
"""
from __future__ import annotations

from typing import Any, Iterable


def _err(prefix: str, msg: str) -> str:
    return f"{prefix}: {msg}"


def validate_top_level(config: dict) -> list[str]:
    """Top-level keys must exist if relevant sections are used."""
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["config root must be a dict"]

    if "project" not in config:
        errors.append(_err("project", "missing required key 'project'"))
    else:
        proj = config["project"]
        for key in ("input_srt", "output_srt", "name"):
            if key not in proj:
                errors.append(_err("project", f"missing required key '{key}'"))

    mode = (config.get("translation", {}) or {}).get("mode", "uncen")
    if mode not in ("default", "uncen", "cen"):
        errors.append(_err("translation.mode", f"got '{mode}', expected one of default/uncen/cen"))

    if "model" not in config and "models" not in config:
        errors.append("missing required key 'model' or 'models'")
    elif "models" in config:
        models = config["models"]
        if not isinstance(models, dict):
            errors.append(_err("models", "must be a dict"))
        else:
            for name, cfg in models.items():
                if not isinstance(cfg, dict):
                    errors.append(_err(f"models.{name}", "must be a dict"))
                    continue
                if "name" not in cfg:
                    errors.append(_err(f"models.{name}", "missing required key 'name'"))
                if "ollama_url" in cfg and not str(cfg["ollama_url"]).startswith(("http://", "https://")):
                    errors.append(_err(f"models.{name}.ollama_url", "must start with http:// or https://"))

    # Numeric ranges
    for key_path, lo, hi in [
        ("model.num_predict", 64, 8192),
        ("model.temperature", 0.0, 2.0),
        ("model.num_ctx", 512, 32768),
        ("pipeline.num_workers", 1, 32),
        ("pipeline.max_retries", 0, 20),
        ("pipeline.checkpoint_interval", 1, 1000),
    ]:
        section, _, key = key_path.partition(".")
        section_cfg = config.get(section, {}) or {}
        if key in section_cfg:
            v = section_cfg[key]
            if not isinstance(v, (int, float)):
                errors.append(_err(key_path, f"must be number, got {type(v).__name__}"))
            elif v < lo or v > hi:
                errors.append(_err(key_path, f"value {v} out of range [{lo}, {hi}]"))

    return errors


def validate_glossary(entries: Iterable[dict]) -> list[str]:
    """Each glossary entry must have source/target strings."""
    errors: list[str] = []
    for i, e in enumerate(entries or []):
        if not isinstance(e, dict):
            errors.append(_err(f"glossary[{i}]", "must be a dict"))
            continue
        if not e.get("source"):
            errors.append(_err(f"glossary[{i}].source", "empty"))
        if not e.get("target"):
            errors.append(_err(f"glossary[{i}].target", "empty"))
    return errors


def validate_all(config: dict) -> list[str]:
    """Run all validators and aggregate errors."""
    errors = validate_top_level(config)
    errors.extend(validate_glossary(config.get("glossary", [])))
    return errors