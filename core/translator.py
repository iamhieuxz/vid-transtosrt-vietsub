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