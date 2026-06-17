import os
import requests
import logging
import time
import json
import re
import threading
from json_repair import repair_json

logger = logging.getLogger(__name__)

class TranslatorService:
    def __init__(self, model_name, ollama_url, temperature=0.1, repeat_penalty=1.2,
                 num_ctx=4096, num_predict=1024, timeout=120,
                 circuit_breaker_threshold=5, circuit_breaker_cooldown=60):
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
        self._error_count = 0
        self._last_failure_time = 0
        self._lock = threading.Lock()

    def generate(self, prompt, temperature=None, num_predict=None):
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
        try:
            resp = requests.post(self.url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            with self._lock:
                self._error_count = 0
            return resp.json()["response"].strip()
        except Exception as e:
            with self._lock:
                self._error_count += 1
                self._last_failure_time = time.time()
            raise e

    def extract_json(self, text):
        # chính: json_repair
        try:
            return json.loads(repair_json(text))
        except Exception as e:
            logger.warning(f"json_repair failed: {e}")
            # fallback non-greedy
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except:
                    pass
            return None