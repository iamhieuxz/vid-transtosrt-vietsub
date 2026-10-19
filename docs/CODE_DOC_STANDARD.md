# Code Documentation Standard (vid-transtosrt-vietsub-clone)

This project is a Vietnamese subtitle translation pipeline (JA/KO/ZH/EN → VI)
with Ollama LLM backend and Whisper transcription.

## Documentation Language Rule

**Code identifiers, comments, log messages, error strings**: English
- All new code uses English for identifiers, docstrings, inline comments, log messages, error messages.
- Rationale: makes code readable to the global Python community, easier to grep, no encoding issues.

**User-facing strings (Rich console output, GUI labels, CLI prompts)**: Vietnamese
- All text the user sees in the terminal, GUI, or SRT output is Vietnamese.
- Use `console.print("[cyan]...tiếng Việt...[/cyan]")` for rich formatting.

**Config file content (YAML)**: Vietnamese for user-edited values, English for keys
- Keys: English (`source`, `target`, `glossary`).
- Values: Vietnamese for display names, original (JA/KO/ZH) for source content.

## Summary Table

| Layer | Language |
|-------|----------|
| Variable / function names | English |
| Docstrings | English |
| Inline comments | English |
| Logger messages | English |
| Exception messages | English |
| Console output (`console.print`) | Vietnamese |
| GUI labels | Vietnamese |
| CLI prompts (`input()`) | Vietnamese |
| SRT output (final deliverable) | Vietnamese |
| Config keys | English |
| Config display values | Vietnamese |

## Examples

```python
# OK
def translate_text(text: str) -> str:
    """Translate one Japanese line to Vietnamese."""
    logger.info("Translation succeeded")
    return result

# OK
console.print(f"[cyan]Đã dịch {count} dòng[/cyan]")

# NOT OK (Vietnamese in code)
def dich_van_ban(text: str) -> str:
    """Dịch một câu tiếng Nhật sang tiếng Việt."""
```

## Migration Note

Existing modules still contain Vietnamese identifiers/comments/log messages
(many inherited from the original Tiếng Việt codebase). New code follows the
standard above. Future refactors may standardize legacy code opportunistically
— do not bulk-replace without semantic review.