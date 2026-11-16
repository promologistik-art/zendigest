import asyncio
import json
import logging
import time
from typing import Any

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

CATEGORIES = {"Кино", "Сериалы", "Книги", "Музыка"}
RETRYABLE_CODES = (408, 429, 500, 502, 503, 504)


class Editor:
    """Selects fresh, image-backed digest stories and writes compact Telegram copy."""

    def __init__(self, key: str, model: str, fallback_model: str = "gemini-3.5-flash-lite"):
        self.client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=30_000),
        )
        self.model = model
        self.fallback_model = fallback_model
        self.batch_size = 15

    @staticmethod
    def _status_code(exc: Exception):
        return getattr(exc, "status_code", None) or getattr(exc, "code", None)

    @classmethod
    def _is_quota_exhausted(cls, exc: Exception) -> bool:
        code = cls._status_code(exc)
        if code == 429 or str(code) == "429":
            return True
        message = str(exc).lower()
        return "resource_exhausted" in message or "quota exceeded" in message

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, "status_code", None)
        if code in RETRYABLE_CODES:
            return True
        message = str(exc).lower()
        return any(marker in message for marker in (
            "503", "unavailable", "429", "resource_exhausted", "500",
            "502", "504", "timeout", "timed out", "connection reset",
            "temporarily unavailable",
        ))

    def _generate(self, prompt: str, model: str) -> str:
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.25,
                max_output_tokens=6000,
                response_mime_type="application/json",
            ),
        )
        if not response.text or not response.text.strip():
            raise ValueError(f"{model} returned an empty response")
        return response.text

    def _request(self, prompt: str) -> Any:
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)
        last_error = None

        for model_index, model in enumerate(models):
            for attempt, delay in enumerate((0, 3), start=1):
                if delay:
                    time.sleep(delay)
                try:
                    return json.loads(self._generate(prompt, model))
                except json.JSONDecodeError as exc:
                    last_error = exc
                    log.warning("Gemini returned invalid JSON: model=%s attempt=%s", model, attempt)
                    if attempt == 1:
                        prompt += (
                            "\n\nПредыдущий ответ был некорректным JSON. "
                            "Повтори строго один корректный JSON-массив без Markdown."
                        )
                        continue
                    break
                except Exception as exc:
                    last_error = exc
                    code = self._status_code(exc)
                    if not self._is_retryable(exc):
                        log.error("Gemini request failed: model=%s code=%s type=%s",
                                  model, code, type(exc).__name__)
                        raise
                    if self._is_quota_exhausted(exc):
                        log.warning("Gemini quota exhausted: model=%s; switching model", model)
                        break
                    log.warning("Gemini temporary failure: model=%s attempt=%s code=%s type=%s",
                                model, attempt, code, type(exc).__name__)
                    if attempt == 2:
                        break
            if model_index + 1 < len(models):
                log.warning("Switching Gemini model: %s -> %s", model, models[model_index + 1])

        raise RuntimeError("All Gemini models failed; no digest was produced") from last_error

    @staticmethod
    def _clean(parsed: Any, allowed_urls: set[str], limit: int) -> list[dict]:
        if not isinstance(parsed, list):
            raise ValueError("Gemini response must be a JSON array")
        clean = []
        seen = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title_ru = item.get("title_ru")
            summary = item.get("summary")
            why = item.get("why")
            category = item.get("category")
            if (
                not isinstance(url, str) or url not in allowed_urls or url in seen
                or not isinstance(title_ru, str) or not title_ru.strip()
                or not isinstance(summary, str) or not summary.strip()
                or not isinstance(why, str) or not why.strip()
                or category not in CATEGORIES
            ):
                continue
            clean.append({
                "url": url,
                "title_ru": title_ru.strip()[:180],
                "category": category,
                "summary": summary.strip()[:720],
                "why": why.strip()[:220],
            })
            seen.add(url)
            if len(clean) >= limit:
                break
        return clean

    @staticmethod
    def _payload(articles: list[dict]) -> list[dict]:
        return [{
            "url": article["url"],
            "title": article["title"],
            "description": article.get("description", "")[:2000],
            "source": article.get("source", ""),
            "date": article["published_at"],
        } for article in articles]

    @staticmethod
    def _prompt(data: list[dict], limit: int, final: bool = False) -> str:
        intro = (
            "Ты выпускающий редактор ZenDigest. Сравни материалы и выбери самые значимые."
            if final else
            "Ты редактор русскоязычного культурного медиа ZenDigest. Отбери сильные новости."
        )
        return f"""{intro}
Темы: кино, сериалы, книги, музыка; Россия и весь мир.
Приоритет: официальные анонсы и даты релизов, премьеры, награды, крупные культурные события,
важные релизы, заметные изменения в индустрии. Исключи слухи, рекламу, слабые перепечатки,
дубли и новости, которые не имеют существенного культурного значения.

Для каждой новости верни поля:
url — строго дословно из входных данных;
title_ru — точный естественный заголовок на русском, без добавления фактов;
category — строго одно из: Кино, Сериалы, Книги, Музыка;
summary — содержательная заметка на 3–5 предложений, ориентир 450–650 знаков.
Объясни, что произошло, основные подтверждённые подробности и необходимый контекст.
Не пересказывай только заголовок. Используй только сведения из входного материала;
не выдумывай участников, даты, цифры, цитаты, рейтинги, платформы или обстоятельства;
why — одно короткое предложение о культурной значимости, до 150 знаков.
Не включай URL изображения и не создавай ссылок. Если фактов недостаточно для
содержательной заметки, пропусти новость. Не заполняй лимит искусственно.
Верни только JSON-массив, без Markdown и пояснений. Максимум: {limit}.
Материалы:
{json.dumps(data, ensure_ascii=False)}"""

    def _edit(self, articles: list[dict], limit: int) -> list[dict]:
        if not articles:
            return []
        batches = [
            articles[index:index + self.batch_size]
            for index in range(0, len(articles), self.batch_size)
        ]
        candidates = []
        for batch_number, batch in enumerate(batches, start=1):
            selected = self._clean(
                self._request(self._prompt(self._payload(batch), limit)),
                {item["url"] for item in batch}, limit,
            )
            candidates.extend(selected)
            log.info("Gemini batch %s/%s: input=%s selected=%s",
                     batch_number, len(batches), len(batch), len(selected))

        if len(candidates) <= limit:
            return candidates
        originals = {item["url"]: item for item in articles}
        final_data = []
        for candidate in candidates:
            original = originals[candidate["url"]]
            final_data.append({
                "url": candidate["url"], "title": original["title"],
                "description": original.get("description", ""),
                "source": original.get("source", ""),
                "date": original["published_at"],
            })
        return self._clean(
            self._request(self._prompt(final_data, limit, final=True)),
            {item["url"] for item in candidates}, limit,
        )

    async def edit(self, articles: list[dict], limit: int) -> list[dict]:
        if not articles:
            return []
        return await asyncio.to_thread(self._edit, articles, limit)
