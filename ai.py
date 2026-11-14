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
    """Selects digest stories with bounded retries and a tested fallback model."""

    def __init__(
        self,
        key: str,
        model: str,
        fallback_model: str = "gemini-3.5-flash-lite",
    ):
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
        return any(
            marker in message
            for marker in (
                "503", "unavailable", "429", "resource_exhausted",
                "500", "502", "504", "timeout", "timed out",
                "connection reset", "temporarily unavailable",
            )
        )

    def _generate(self, prompt: str, model: str) -> str:
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=5000,
                response_mime_type="application/json",
            ),
        )
        text = response.text
        if not text or not text.strip():
            raise ValueError(f"{model} returned an empty response")
        return text

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
                    raw = self._generate(prompt, model)
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    last_error = exc
                    log.warning(
                        "Gemini returned invalid JSON: model=%s attempt=%s error=%s",
                        model, attempt, exc,
                    )
                    # One corrective request on the same model.
                    if attempt == 1:
                        prompt += (
                            "\n\nВАЖНО: предыдущий ответ оказался некорректным JSON. "
                            "Повтори ответ строго как один корректный JSON-массив, "
                            "без Markdown и пояснений."
                        )
                        continue
                    break
                except Exception as exc:
                    last_error = exc
                    code = self._status_code(exc)
                    if not self._is_retryable(exc):
                        log.exception(
                            "Gemini request failed: model=%s code=%s",
                            model, code,
                        )
                        raise

                    if self._is_quota_exhausted(exc):
                        log.warning(
                            "Gemini quota exhausted: model=%s code=%s; "
                            "skipping retry and switching to fallback if available. error=%s",
                            model, code, str(exc)[:500],
                        )
                        break

                    log.warning(
                        "Gemini temporary failure: model=%s attempt=%s/2 "
                        "code=%s error=%s",
                        model, attempt, code, str(exc)[:500],
                    )
                    # Retry transient service/network errors once, then switch models.
                    if attempt == 2:
                        break

            if model_index + 1 < len(models):
                log.warning(
                    "Switching Gemini model: %s -> %s",
                    model, models[model_index + 1],
                )

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
            summary = item.get("summary")
            why = item.get("why")
            if (
                not isinstance(url, str)
                or url not in allowed_urls
                or url in seen
                or not isinstance(summary, str)
                or not summary.strip()
                or not isinstance(why, str)
                or not why.strip()
            ):
                continue

            category = item.get("category")
            if category not in CATEGORIES:
                category = "Культура"

            clean.append({
                "url": url,
                "category": category,
                "summary": summary.strip()[:650],
                "why": why.strip()[:250],
            })
            seen.add(url)
            if len(clean) >= limit:
                break
        return clean

    @staticmethod
    def _payload(articles: list[dict]) -> list[dict]:
        return [
            {
                "url": article["url"],
                "title": article["title"],
                "description": article.get("description", "")[:600],
                "source": article.get("source", ""),
                "date": article["published_at"],
            }
            for article in articles
        ]

    @staticmethod
    def _prompt(data: list[dict], limit: int, final: bool = False) -> str:
        instruction = (
            "Ты главный редактор русского дайджеста ZenDigest о кино, сериалах, "
            "книгах и музыке. Сравни уже отобранные материалы и оставь самые "
            "значимые, без повторов и искусственного заполнения лимита."
            if final else
            "Ты редактор русского дайджеста ZenDigest о кино, сериалах, книгах и музыке. "
            "Выбери наиболее значимые новости из этой партии. Не заполняй лимит искусственно."
        )
        return f""" {instruction}
Приоритет: подтверждённые анонсы, даты релизов, награды, важные культурные события,
крупные релизы и значимые изменения в индустрии. Исключи слухи, рекламу,
малозначительные перепечатки и дубли.
Верни только JSON-массив объектов с полями:
url (строго дословно из входных данных),
category (одно из: Кино, Сериалы, Книги, Музыка),
summary (1-2 коротких предложения по-русски),
why (коротко объясни значимость новости).
Используй только информацию из входных материалов. Не выдумывай факты,
даты, рейтинги, платформы и подробности. Если достойных новостей нет — [].
Максимум новостей: {limit}.
Входные материалы:
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
            data = self._payload(batch)
            prompt = self._prompt(data, limit)
            parsed = self._request(prompt)
            selected = self._clean(
                parsed,
                {item["url"] for item in batch},
                limit,
            )
            candidates.extend(selected)
            log.info(
                "Gemini batch %s/%s: input=%s selected=%s",
                batch_number, len(batches), len(batch), len(selected),
            )

        if not candidates:
            return []

        if len(candidates) <= limit:
            return candidates

        # Final ranking only sees already validated candidates and their source titles.
        originals = {item["url"]: item for item in articles}
        final_data = []
        for candidate in candidates:
            original = originals[candidate["url"]]
            final_data.append({
                "url": candidate["url"],
                "title": original["title"],
                "source": original.get("source", ""),
                "summary": candidate["summary"],
                "why": candidate["why"],
                "category": candidate["category"],
            })

        parsed = self._request(self._prompt(final_data, limit, final=True))
        return self._clean(
            parsed,
            {item["url"] for item in candidates},
            limit,
        )

    async def edit(self, articles: list[dict], limit: int) -> list[dict]:
        if not articles:
            return []
        return await asyncio.to_thread(self._edit, articles, limit)
