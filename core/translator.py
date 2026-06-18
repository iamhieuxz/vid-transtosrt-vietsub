import os
import requests
import logging
import time
import json
import re
import threading
from typing import List, Optional
from json_repair import repair_json

logger = logging.getLogger(__name__)

STATUS_ICONS = {
    'warning': '[!]',
}


class TranslatorService:
    def __init__(self, model_name, ollama_url, temperature=0.1, repeat_penalty=1.2,
                 num_ctx=4096, num_predict=1024, timeout=120,
                 circuit_breaker_threshold=5, circuit_breaker_cooldown=60,
                 max_retries=3, retry_delay=2):
        self.model = model_name
        self.url = ollama_url or os.environ.get(
            'OLLAMA_URL', 'http://localhost:11434/api/generate'
        )
        self.temperature = temperature
        self.repeat_penalty = repeat_penalty
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.timeout = timeout
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown = circuit_breaker_cooldown
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._error_count = 0
        self._last_failure_time = 0
        self._lock = threading.Lock()

    def generate(self, prompt, temperature=None, num_predict=None, _retries=None):
        retries = _retries if _retries is not None else self.max_retries

        with self._lock:
            if self._error_count >= self.circuit_breaker_threshold:
                if time.time() - self._last_failure_time < self.circuit_breaker_cooldown:
                    raise Exception("Circuit breaker open")
                else:
                    self._error_count = 0

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else self.temperature,
                "repeat_penalty": self.repeat_penalty,
                "num_ctx": self.num_ctx,
                "num_predict": num_predict or self.num_predict,
            }
        }
        for attempt in range(retries + 1):
            try:
                resp = requests.post(self.url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("response", "").strip()

                if not raw:
                    raise ValueError("Empty response from model")

                with self._lock:
                    self._error_count = 0
                return raw

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                logger.warning(f"{STATUS_ICONS.get('warning', '[!]')} Ollama connection error (attempt {attempt+1}/{retries+1}): {e}")
                if attempt < retries:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    with self._lock:
                        self._error_count += 1
                        self._last_failure_time = time.time()
                    raise Exception(f"Ollama unreachable after {retries+1} attempts: {e}")

            except Exception as e:
                logger.warning(f"{STATUS_ICONS.get('warning', '[!]')} Generate error (attempt {attempt+1}/{retries+1}): {e}")
                if attempt < retries:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    with self._lock:
                        self._error_count += 1
                        self._last_failure_time = time.time()
                    raise

    def extract_json(self, text):
        # chính: json_repair
        try:
            return json.loads(repair_json(text))
        except Exception as e:
            logger.warning(f"json_repair failed: {e}")
            # fallback greedy: tìm JSON array LỚN NHẤT trong response
            # dùng greedy để tránh khớp [] trống ở đầu
            matches = re.findall(r'\[.*\]', text, re.DOTALL)
            for match in reversed(matches):  # thử từ dài nhất -> ngắn nhất
                try:
                    parsed = json.loads(match)
                    if isinstance(parsed, list):
                        logger.info(f"Fallback JSON extraction succeeded with {len(parsed)} items")
                        return parsed
                except Exception:
                    pass
            logger.error("All JSON extraction methods failed")
            return None

    def fallback_translate(
        self,
        source_lines: List[str],
        src_lang: str,
        tgt_lang: str,
        glossary_terms: Optional[List[dict]] = None,
    ) -> Optional[List[str]]:
        """
        Phase-2 fallback: dịch từng chunk 2-3 dòng bằng prompt đơn giản,
        không yêu cầu JSON output, rồi ghép lại.
        Trả về list translation theo đúng thứ tự source_lines, hoặc None nếu thất bại.
        """
        # Ghép thành chunk nhỏ để LLM dịch từng phần
        chunk_size = 3
        translations = []

        for i in range(0, len(source_lines), chunk_size):
            chunk = source_lines[i : i + chunk_size]
            chunk_lines = "\n".join(f"{j+1}. {line}" for j, line in enumerate(chunk))

            # Xây dựng phần glossary nếu có
            glossary_block = ""
            if glossary_terms:
                lines = [f"- {t['source_term']} -> {t['target_term']}" for t in glossary_terms]
                glossary_block = "MANDATORY GLOSSARY:\n" + "\n".join(lines) + "\n"

            # Prompt đơn giản, không JSON — yêu cầu 1 dòng mỗi input
            prompt = (
                f"Translate from {src_lang} to {tgt_lang}. "
                f"Return only the translations, one per line, no numbering, no extra text.\n"
                f"{glossary_block}"
                f"Lines to translate:\n{chunk_lines}"
            )

            raw = self.generate(prompt, temperature=0.1)
            raw_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]

            # Map: mỗi input line -> 1 output line
            # Nếu raw_lines nhiều hơn chunk, cắt. Ít hơn thì bổ sung ""
            for j, line in enumerate(chunk):
                if j < len(raw_lines):
                    translations.append(raw_lines[j].strip())
                else:
                    # Dòng còn lại thử dịch độc lập bằng retry ngắn
                    retry_raw = self._translate_single(line, src_lang, tgt_lang, glossary_terms)
                    translations.append(retry_raw if retry_raw else line)

        return translations if translations else None

    def _translate_single(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        glossary_terms: Optional[List[dict]] = None,
    ) -> Optional[str]:
        """Dịch 1 câu độc lập, trả về chuỗi thuần (không JSON)."""
        glossary_block = ""
        if glossary_terms:
            lines = [f"- {t['source_term']} -> {t['target_term']}" for t in glossary_terms]
            glossary_block = "MANDATORY GLOSSARY:\n" + "\n".join(lines) + "\n"

        prompt = (
            f"Translate this {src_lang} text to {tgt_lang}. "
            f"Return ONLY the translation, nothing else.\n"
            f"{glossary_block}"
            f"Text: {text}"
        )
        try:
            raw = self.generate(prompt, temperature=0.1)
            return raw.strip().split("\n")[0].strip()
        except Exception as e:
            logger.warning(f"_translate_single failed for '{text[:30]}...': {e}")
            return None

    def raw_translate(
        self,
        source_lines: List[str],
        src_lang: str,
        tgt_lang: str,
        glossary_terms: Optional[List[dict]] = None,
    ) -> Optional[List[str]]:
        """
        Pass 1 của recovery — dịch thô word-by-word / chunk nhỏ, không cần ngữ cảnh.
        Trả về list translation theo thứ tự source_lines, hoặc None nếu thất bại.
        """
        translations = []
        chunk_size = 3

        for i in range(0, len(source_lines), chunk_size):
            chunk = source_lines[i:i + chunk_size]
            chunk_text = "\n".join(f"{j+1}. {line}" for j, line in enumerate(chunk))

            glossary_block = ""
            if glossary_terms:
                glossary_block = "MANDATORY GLOSSARY:\n" + "\n".join(
                    f"- {t['source_term']} -> {t['target_term']}" for t in glossary_terms
                ) + "\n"

            prompt = (
                f"Translate the following {src_lang} lines to {tgt_lang} literally, "
                f"preserving meaning word-by-word.\n"
                f"{glossary_block}"
                f"Return ONLY the translations, one per line, matching the order:\n"
                f"{chunk_text}"
            )

            try:
                raw = self.generate(prompt, temperature=0.0)
                raw_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
                for j, _ in enumerate(chunk):
                    if j < len(raw_lines):
                        translations.append(raw_lines[j].strip())
                    else:
                        single = self._translate_single(source_lines[i + j], src_lang, tgt_lang, glossary_terms)
                        translations.append(single if single else source_lines[i + j])
            except Exception as e:
                logger.warning(f"raw_translate chunk {i} failed: {e}, falling back to single")
                for j in range(len(chunk)):
                    single = self._translate_single(source_lines[i + j], src_lang, tgt_lang, glossary_terms)
                    translations.append(single if single else source_lines[i + j])

        return translations if translations else None

    def polish_translate(
        self,
        source_lines: List[str],
        raw_translations: List[str],
        src_lang: str,
        tgt_lang: str,
        context_before: str = "",
        context_after: str = "",
    ) -> Optional[List[str]]:
        """
        Pass 2 của recovery — làm mượt câu từ từ bản dịch thô.
        Cải thiện: ngữ pháp, cách dùng tiếng lóng, ngữ điệu, ngữ cảnh.
        Trả về list đã trau chuốt theo thứ tự source_lines, hoặc None nếu thất bại.
        """
        # Ghép thành cặp để dịch theo chunk
        pairs = "\n".join(
            f"ORIGINAL: {s}\nROUGH: {r}" for s, r in zip(source_lines, raw_translations)
        )

        prompt = (
            f"You are a professional subtitle editor refining {src_lang}→{tgt_lang} translations.\n"
            f"Given the original lines and their rough translations, rewrite each line to be:\n"
            f"  - Natural, fluent, and contextually accurate\n"
            f"  - Using colloquial/slang expressions where appropriate\n"
            f"  - Consistent in tone and register with the surrounding context\n"
            f"  - Preserving the original meaning and subtitle rhythm\n\n"
            f"Context before:\n{context_before or '(none)'}\n\n"
            f"Context after:\n{context_after or '(none)'}\n\n"
            f"Rewrite each line:\n{pairs}\n\n"
            f"Return ONLY the refined translations, one per line, in the same order. No numbering, no extra text."
        )

        try:
            raw = self.generate(prompt, temperature=0.2)
            lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
            # Map the result lines back to source_lines in order
            result = []
            for j in range(len(source_lines)):
                if j < len(lines):
                    result.append(lines[j].strip())
                elif j < len(raw_translations):
                    result.append(raw_translations[j])  # fallback giữ nguyên rough
                else:
                    result.append(source_lines[j])
            logger.info(f"polish_translate: refined {len(result)} lines")
            return result
        except Exception as e:
            logger.warning(f"polish_translate failed: {e}, falling back to rough translations")
            return raw_translations


# -----------------------------------------------------------------------------
# Prompt-driven methods (use prompts/<src>-<tgt>.yaml)
# -----------------------------------------------------------------------------
from prompts import registry as _prompt_registry  # noqa: E402


def _resolve_lang_pair(source_lang: str, target_lang: str) -> str:
    """Map a (source, target) display-name pair to a ``<src>-<tgt>`` key.

    Tries the canonical key first; if missing, falls back to a normalized
    short-form lookup (e.g. "Chinese" → "zh").
    """
    short_map = {
        'chinese': 'zh',
        'japanese': 'ja',
        'english': 'en',
        'korean': 'ko',
        'vietnamese': 'vi',
        'tiếng trung': 'zh',
        'tiếng nhật': 'ja',
        'tiếng anh': 'en',
        'tiếng hàn': 'ko',
        'tiếng việt': 'vi',
    }
    src = short_map.get(source_lang.strip().lower(), source_lang.strip().lower())
    tgt = short_map.get(target_lang.strip().lower(), target_lang.strip().lower())
    pair = f"{src}-{tgt}"
    if pair in _prompt_registry.available():
        return pair
    raise FileNotFoundError(
        f"No prompt file for language pair '{source_lang}' → '{target_lang}' "
        f"(resolved to '{pair}', available: {_prompt_registry.available()})"
    )


def _render_glossary_block(prompt_set, glossary_terms):
    if not glossary_terms:
        return ""
    lines = []
    for t in glossary_terms:
        line = f"- {t['source_term']} -> {t['target_term']}"
        if t.get('context_hint'):
            line += f" (context: {t['context_hint']})"
        lines.append(line)
    return "MANDATORY GLOSSARY:\n" + "\n".join(lines) + "\n"


class PromptedTranslatorMixin:
    """Methods that build prompts from per-pair YAML templates.

    Intended to be mixed into ``TranslatorService`` (kept here as a mixin so the
    legacy inline-prompt methods continue to work unchanged).
    """

    def _pair(self, src_lang: str, tgt_lang: str):
        pair = _resolve_lang_pair(src_lang, tgt_lang)
        return pair, _prompt_registry.get(pair)

    # ---- chunk helpers ----
    def _chunk_lines(self, source_lines, chunk_size=8):
        for i in range(0, len(source_lines), chunk_size):
            yield source_lines[i:i + chunk_size]

    def _numbered(self, lines):
        return "\n".join(f"{i+1}. {l}" for i, l in enumerate(lines))

    def _pairs(self, source_lines, raw_lines):
        return "\n".join(
            f"ORIGINAL: {s}\nROUGH: {r}" for s, r in zip(source_lines, raw_lines)
        )

    # ---- public methods ----
    def raw_translate_pair(
        self,
        source_lines: List[str],
        src_lang: str,
        tgt_lang: str,
        glossary_terms=None,
        chunk_size: int = 8,
    ) -> Optional[List[str]]:
        """Pass 1: chunked literal translation using the pair's ``raw_translate``
        (or ``literal`` for multi-pass pairs like ja-vi) section.
        """
        pair, ps = self._pair(src_lang, tgt_lang)
        section = "literal" if ps.has("literal") else (
            "raw_translate" if ps.has("raw_translate") else None
        )
        if section is None:
            raise ValueError(f"Pair '{pair}' has no raw_translate/literal section")

        glossary_block = _render_glossary_block(ps, glossary_terms)
        translations: List[str] = []

        for chunk in self._chunk_lines(source_lines, chunk_size=chunk_size):
            numbered = self._numbered(chunk)
            base = ps.render(
                section,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                lines=numbered,
            )
            # Inject glossary right after system instructions; place at end of header
            prompt = base.replace(
                "Các dòng cần dịch:\n{lines}",
                f"{glossary_block}Các dòng cần dịch:\n{numbered}"
            ) if glossary_block else base

            try:
                raw = self.generate(prompt, temperature=0.1)
            except Exception as e:
                logger.warning(f"raw_translate_pair: generate failed: {e}")
                return None

            out_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
            for j, original in enumerate(chunk):
                if j < len(out_lines):
                    translations.append(self._strip_leading_number(out_lines[j]))
                else:
                    single = self._translate_single(
                        original, src_lang, tgt_lang, glossary_terms
                    )
                    translations.append(single if single else original)
        return translations if translations else None

    def fallback_translate_pair(
        self,
        source_lines: List[str],
        src_lang: str,
        tgt_lang: str,
        glossary_terms=None,
        chunk_size: int = 3,
    ) -> Optional[List[str]]:
        """Pass 2 fallback: smaller chunks, simpler prompt."""
        pair, ps = self._pair(src_lang, tgt_lang)
        section = "fallback_translate" if ps.has("fallback_translate") else (
            "literal" if ps.has("literal") else "raw_translate"
        )

        glossary_block = _render_glossary_block(ps, glossary_terms)
        translations: List[str] = []

        for chunk in self._chunk_lines(source_lines, chunk_size=chunk_size):
            numbered = self._numbered(chunk)
            base = ps.render(
                section,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                lines=numbered,
            )
            prompt = f"{glossary_block}{base}" if glossary_block else base

            try:
                raw = self.generate(prompt, temperature=0.1)
            except Exception as e:
                logger.warning(f"fallback_translate_pair: generate failed: {e}")
                return None

            out_lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
            for j, original in enumerate(chunk):
                if j < len(out_lines):
                    translations.append(self._strip_leading_number(out_lines[j]))
                else:
                    single = self._translate_single(
                        original, src_lang, tgt_lang, glossary_terms
                    )
                    translations.append(single if single else original)
        return translations if translations else None

    def refine_pair(
        self,
        source_lines: List[str],
        raw_translations: List[str],
        src_lang: str,
        tgt_lang: str,
        glossary_terms=None,
    ) -> Optional[List[str]]:
        """Pass 2 (or 'polish' for multi-pass pairs): refine rough translations.

        Uses the pair's ``refine`` section if available; otherwise falls back to
        ``polish`` (ja-vi) or the legacy in-line prompt.
        """
        pair, ps = self._pair(src_lang, tgt_lang)
        section = None
        for candidate in ("refine", "polish"):
            if ps.has(candidate):
                section = candidate
                break
        if section is None:
            return raw_translations  # nothing to do

        glossary_block = _render_glossary_block(ps, glossary_terms)
        pairs_text = self._pairs(source_lines, raw_translations)
        prompt = ps.render(
            section,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            pairs=pairs_text,
        )
        if glossary_block:
            prompt = f"{glossary_block}\n{prompt}"

        try:
            raw = self.generate(prompt, temperature=0.2)
        except Exception as e:
            logger.warning(f"refine_pair: generate failed: {e}, keeping rough translations")
            return raw_translations

        parsed = self.extract_json(raw)
        if not parsed or not isinstance(parsed, list):
            return raw_translations

        out: List[str] = []
        for j, original in enumerate(source_lines):
            match = None
            for item in parsed:
                idx = item.get("index") if isinstance(item, dict) else None
                if idx == j + 1:
                    match = item.get("translation") or item.get("text")
                    break
            out.append(match if match else raw_translations[j])
        return out

    def qa_pair(
        self,
        source_lines: List[str],
        translations: List[str],
        src_lang: str,
        tgt_lang: str,
        glossary_terms=None,
    ) -> List[dict]:
        """Pass 3 (only for multi-pass pairs): QA review of translations.

        Returns list of {index, ok, issue, suggestion}. Empty list if the pair
        has no QA section.
        """
        pair, ps = self._pair(src_lang, tgt_lang)
        if not ps.has("qa"):
            return []

        glossary_block = _render_glossary_block(ps, glossary_terms)
        pairs_text = self._pairs(source_lines, translations)
        prompt = ps.render(
            "qa",
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            pairs=pairs_text,
        )
        if glossary_block:
            prompt = f"{glossary_block}\n{prompt}"

        try:
            raw = self.generate(prompt, temperature=0.0)
        except Exception as e:
            logger.warning(f"qa_pair: generate failed: {e}")
            return []

        parsed = self.extract_json(raw)
        if not isinstance(parsed, list):
            return []
        out: List[dict] = []
        for item in parsed:
            if not isinstance(item, dict) or "index" not in item:
                continue
            out.append({
                "index": int(item.get("index", 0)),
                "ok": bool(item.get("ok", False)),
                "issue": item.get("issue", ""),
                "suggestion": item.get("suggestion", ""),
            })
        return out

    @staticmethod
    def _strip_leading_number(line: str) -> str:
        """Remove "1. ", "2) ", etc. at the start of a line."""
        import re as _re
        return _re.sub(r'^\s*\d+[\.\)]\s*', '', line).strip()


# Attach the mixin methods to TranslatorService. We can't change __bases__
# safely for a class with a different deallocator, so we simply rebind each
# public method onto the class object.
for _name in (
    "raw_translate_pair",
    "fallback_translate_pair",
    "refine_pair",
    "qa_pair",
):
    setattr(TranslatorService, _name, getattr(PromptedTranslatorMixin, _name))
del _name