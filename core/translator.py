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
        base_url = ollama_url or os.environ.get('OLLAMA_URL', 'http://localhost:11434')
        # Auto-detect API type and build full URL
        if '/api/chat' in base_url:
            self.api_type = 'chat'
            self.url = base_url.rstrip('/')  # already has /api/chat
        elif '/api/generate' in base_url:
            self.api_type = 'generate'
            self.url = base_url.rstrip('/')  # already has /api/generate
        elif ('/v1/chat/completions' in base_url or '/v1/completions' in base_url
              or '/v1/' in base_url or base_url.rstrip('/').endswith('/v1')):
            # llama.cpp / llama-server (OpenAI-compatible). Server phai chay --jinja
            # thi template moi duoc apply, giong cach Ollama /api/generate wrap prompt.
            self.api_type = 'openai'
            root = base_url.rstrip('/')
            for suffix in ('/v1/chat/completions', '/v1/completions', '/v1'):
                if root.endswith(suffix):
                    root = root[:-len(suffix)]
                    break
            self.base_url = root
            self.url = self.base_url + '/v1/chat/completions'
        else:
            # Default use generate API (mo ta model ho tro tot hon)
            self.api_type = 'generate'
            self.url = base_url.rstrip('/') + '/api/generate'
        if not hasattr(self, 'base_url'):
            self.base_url = self.url.replace('/api/chat', '').replace('/api/generate', '') \
                .replace('/v1/chat/completions', '').rstrip('/')

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
        self._consecutive_empty = 0  # Track consecutive empty responses

    def generate(self, prompt, temperature=None, num_predict=None, _retries=None):
        retries = _retries if _retries is not None else self.max_retries

        with self._lock:
            if self._error_count >= self.circuit_breaker_threshold:
                if time.time() - self._last_failure_time < self.circuit_breaker_cooldown:
                    raise Exception("Circuit breaker open")
                else:
                    self._error_count = 0

        # Context Guard (Group C2): tự động nâng num_ctx nếu prompt dài vượt ngưỡng
        eff_num_predict = num_predict or self.num_predict
        try:
            from .ja_qa_checker import guard_prompt_num_ctx
            eff_num_ctx = guard_prompt_num_ctx(prompt, self.num_ctx, eff_num_predict)
        except Exception:
            eff_num_ctx = self.num_ctx

        # Build payload based on API type
        if self.api_type == 'chat':
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": temperature if temperature is not None else self.temperature,
                    "repeat_penalty": self.repeat_penalty,
                    "num_ctx": eff_num_ctx,
                    "num_predict": eff_num_predict,
                }
            }
        elif self.api_type == 'openai':
            # llama.cpp /v1/chat/completions. Luu y: num_ctx KH phai tham so theo-request
            # o day - no bi --ctx-size cua server chot ngay luc khoi dong, nen server
            # phai du lon hon buoc cao nhat cua ContextGuard (16384).
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": temperature if temperature is not None else self.temperature,
                "max_tokens": eff_num_predict,
                # llama.cpp chap nhan cac tham so sampler cua no o top-level ngoai OpenAI chuan.
                # Server mac dinh da --repeat-penalty 1.0 (nhat quan voi gia tri duoi day).
                "repeat_penalty": self.repeat_penalty,
                # Lop thu 2 chan thinking (lop 1 la --reasoning-budget 0 cua server).
                # Template nao khong ho tro thi key nay bi bo qua, khong gay loi.
                "chat_template_kwargs": {"enable_thinking": False},
            }
        else:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": temperature if temperature is not None else self.temperature,
                    "repeat_penalty": self.repeat_penalty,
                    "num_ctx": eff_num_ctx,
                    "num_predict": eff_num_predict,
                }
            }

        _grown_once = False
        for attempt in range(retries + 1):
            try:
                resp = requests.post(self.url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()

                # Extract response based on API type
                if self.api_type == 'chat':
                    raw = data.get("message", {}).get("content", "").strip()
                elif self.api_type == 'openai':
                    choice = (data.get("choices") or [{}])[0]
                    raw = (choice.get("message", {}).get("content", "") or "").strip()
                    if raw and choice.get("finish_reason") == "length":
                        # Có nội dung nhưng bị cắt giữa chừng: câu cuối window sẽ cụt/JSON hỏng
                        # mà không ai biết. Nới num_predict 1 lần rồi sinh lại.
                        grown = min(eff_num_predict * 2, 4096)
                        if grown > eff_num_predict and not _grown_once:
                            logger.warning(
                                f"[openai] output bi cat tai max_tokens={eff_num_predict}, "
                                f"nang len {grown} va sinh lai")
                            eff_num_predict = grown
                            payload["max_tokens"] = grown
                            _grown_once = True
                            # Gọi lại tại chỗ (không dùng continue: lần thử cuối sẽ thoát vòng và trả None)
                            resp = requests.post(self.url, json=payload, timeout=self.timeout)
                            resp.raise_for_status()
                            choice = (resp.json().get("choices") or [{}])[0]
                            raw = (choice.get("message", {}).get("content", "") or "").strip()
                        if raw and choice.get("finish_reason") == "length":
                            logger.warning(
                            f"[openai] output van bi cat o max_tokens={eff_num_predict}; "
                            "cau cuoi co the cut")
                    if not raw and choice.get("finish_reason") == "length":
                        # Model bi cat khi dang suy luan -> retry la vo ich, phai giam
                        # token thinking (llama_server.reasoning_budget) hoac num_predict.
                        logger.warning(
                            "[openai] content rong + finish_reason=length: model cham max_tokens "
                            f"({eff_num_predict}) khi dang suy luan, khong phai loi tam thoi")
                else:
                    raw = data.get("response", "").strip()

                # Strip think block nếu model vẫn trả về dù có think:false
                raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

                if not raw:
                    self._consecutive_empty += 1
                    logger.warning(f"{STATUS_ICONS.get('warning', '[!]')} Empty response from model (attempt {attempt+1}/{retries+1}, consecutive={self._consecutive_empty})")
                    # Reset context nếu model trả về empty liên tục
                    if self._consecutive_empty >= 2:
                        logger.info("Multiple empty responses detected, resetting model context...")
                        self.reset_context()
                    if attempt < retries:
                        time.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise ValueError("Empty response from model")

                with self._lock:
                    self._error_count = 0
                    self._consecutive_empty = 0  # Reset counter on success
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

    def reset_circuit_breaker(self):
        """Reset circuit breaker state - goi truoc khi pipeline moi bat dau."""
        with self._lock:
            self._error_count = 0
            self._last_failure_time = 0
        logger.info("Circuit breaker reset")

    def check_ollama_health(self, timeout=3):
        """Kiem tra Ollama con song khong (khong phu thuoc vao circuit breaker).
        Returns True neu Ollama phan hoi duoc /api/tags.
        """
        try:
            if self.api_type == 'openai':
                # llama-server expose /health (200 khi model da san sang)
                resp = requests.get(f"{self.base_url}/health", timeout=timeout)
                return resp.status_code == 200
            tags_url = self.url.replace('/api/generate', '/api/tags').replace('/api/chat', '/api/tags')
            resp = requests.get(tags_url, timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def try_recover_circuit_breaker(self):
        """Thu reset circuit breaker neu Ollama da hoi sinh.
        Tra ve True neu reset thanh cong, False neu Ollama van down.
        """
        with self._lock:
            if self._error_count < self.circuit_breaker_threshold:
                return True  # Khong can reset
        # Kiem tra Ollama con song khong
        if self.check_ollama_health():
            logger.info("Ollama recovered! Auto-resetting circuit breaker.")
            with self._lock:
                self._error_count = 0
                self._last_failure_time = 0
            return True
        return False

    def reset_context(self):
        """Reset model KV cache by unloading + reloading the model.

        P2-fix: previous implementation sent a "." prompt with num_predict=1,
        which only produces a tiny response but DOES NOT clear the model's
        KV cache. Ollama keeps the full conversation context in memory until
        the session ends. The only reliable way to clear state is to unload
        and reload the model via /api/generate (with keep_alive=0 then pull).
        We avoid full unload (slow) and instead use the model's built-in
        session timeout: send a request with `keep_alive: "1s"` so the model
        unloads after 1 second, then warm back up on the next request.

        Strategy:
          1. Try /api/generate with keep_alive: "1s" to evict the model after response.
          2. If endpoint rejects, fall back to the old "." prompt trick.
          3. Either way, track that reset was attempted so callers can verify.

        Returns:
            True if reset succeeded (model unloaded/reloaded cycle started).
        """
        import requests
        if self.api_type == 'openai':
            # llama-server khong giu "session" nhu Ollama: moi request /v1/chat/completions
            # tu do, KV chi duoc dung lai theo prefix cung slot. Thu clear slot qua admin API
            # (bat bang --admin-api); neu khong duoc thi bo qua vi van an toan.
            try:
                r = requests.post(f"{self.base_url}/slots", json={"action": "clear", "id": 0}, timeout=10)
                if r.ok:
                    logger.debug("Context reset OK via /slots clear")
                    return True
            except Exception as e:
                logger.debug(f"/slots clear khong kha dung ({e}) - llama-server stateless, bo qua")
            return True
        base_url = self.url.replace('/api/generate', '').replace('/api/chat', '').rstrip('/')
        try:
            # Strategy 1: keep_alive trick — model unloads 1s after this response.
            # No re-warm needed: the next real translate() call will re-pull.
            payload = {
                "model": self.model,
                "prompt": ".",
                "stream": False,
                "keep_alive": "1s",  # unload model 1s after responding
            }
            resp = requests.post(f"{base_url}/api/generate", json=payload, timeout=30)
            resp.raise_for_status()
            logger.debug("Context reset OK via keep_alive eviction")
            return True
        except Exception as e:
            logger.debug(f"keep_alive reset failed ({e}), falling back to minimal prompt")
            # Strategy 2: best-effort minimal-prompt reset (legacy behavior).
            try:
                fallback_payload = {
                    "model": self.model,
                    "prompt": ".",
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.1, "num_predict": 1},
                }
                resp = requests.post(self.url, json=fallback_payload, timeout=30)
                resp.raise_for_status()
                logger.debug("Context reset OK via minimal prompt fallback")
                return True
            except Exception as e2:
                logger.debug(f"Context reset failed (non-critical): {e2}")
                return False

    def warmup(self, timeout=300):
        """Preload model by sending a dummy request.
        
        This ensures the model is loaded into VRAM before actual translation starts.
        Call this once before the translation pipeline begins.
        
        Args:
            timeout: Timeout in seconds for the warmup request (default 300s = 5min)
        
        Returns:
            True if warmup succeeded, False otherwise
        """
        import requests
        logger.info(f"[WARMUP] Preloading model: {self.model}")

        # Simple warmup prompt - just ask the model to respond with a short word
        warmup_prompt = "Say exactly one word: ready"

        if self.api_type == 'chat':
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": warmup_prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 10,
                }
            }
        elif self.api_type == 'openai':
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": warmup_prompt}],
                "stream": False,
                "temperature": 0.1,
                "max_tokens": 10,
            }
        else:
            payload = {
                "model": self.model,
                "prompt": warmup_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 10,
                }
            }

        try:
            resp = requests.post(self.url, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()

            if self.api_type == 'chat':
                response = data.get("message", {}).get("content", "").strip()
            elif self.api_type == 'openai':
                response = (data.get("choices", [{}])[0]
                                .get("message", {}).get("content", "") or "").strip()
            else:
                response = data.get("response", "").strip()

            # load_duration la rieng cua Ollama; llama-server da load model luc khoi dong
            # nen o day chi can phan hoi la du.
            load_duration = data.get("load_duration", 0) / 1e9  # convert to seconds
            logger.info(f"[WARMUP] Model loaded in {load_duration:.1f}s, response: '{response}'")
            return True

        except requests.exceptions.Timeout:
            logger.warning(f"[WARMUP] Timeout after {timeout}s - model may still be loading, continuing anyway")
            return True  # Don't fail warmup, let actual requests handle it
        except Exception as e:
            logger.warning(f"[WARMUP] Failed: {e}, continuing without warmup")
            return False

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
        DEPRECATED: was used by non-JA recovery path. JA recovery now goes through
        JaTranslator (3-pass). Non-JA recovery uses fallback_translate directly.
        Kept as thin wrapper for one release to avoid breaking external imports.
        """
        logger.warning("TranslatorService.raw_translate is deprecated, use fallback_translate")
        return self.fallback_translate(source_lines, src_lang, tgt_lang, glossary_terms)

    def polish_translate(
        self,
        source_lines: List[str],
        raw_translations: List[str],
        src_lang: str,
        tgt_lang: str,
        context_before: str = "",
        context_after: str = "",
        glossary_terms: Optional[List[dict]] = None,
    ) -> Optional[List[str]]:
        """
        DEPRECATED: was used by non-JA recovery path. JA recovery now uses
        JaTranslator.polish_translate which is keigo-aware. Non-JA mode does not
        require polish pass — fallback_translate already produces usable output.
        Kept as thin wrapper for one release to avoid breaking external imports.
        """
        logger.warning("TranslatorService.polish_translate is deprecated, use JaTranslator.polish_translate")
        return raw_translations
