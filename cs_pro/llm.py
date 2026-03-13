from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass
class LLMResult:
    title: str
    detail: str


class LLMEvaluator:
    def __init__(
        self,
        enabled: bool,
        api_type: str,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int,
        system_prompt: str,
    ) -> None:
        self.enabled = enabled
        self.api_type = (api_type or "openai").strip().lower()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model
        self.timeout = timeout
        self.system_prompt = system_prompt

    async def evaluate(self, match_context: dict) -> LLMResult | None:
        if not self.enabled or not self.api_key:
            return None

        user_prompt = (
            "请基于以下CS2对局数据给出JSON:\n"
            "{\"title\":\"8-16字打法风格\",\"detail\":\"80-180字详细评价与建议\"}\n"
            "仅输出JSON，不要额外文本。\n\n"
            f"数据: {json.dumps(match_context, ensure_ascii=False)}"
        )

        try:
            content = await self._call_llm(user_prompt)
        except Exception:
            return None

        obj = self._extract_json(content)
        if not obj:
            return self._fallback(content)

        title = str(obj.get("title") or "风格待定").strip()
        detail = str(obj.get("detail") or "本局信息不足，建议继续观察多场数据。").strip()
        if not title:
            title = "风格待定"
        if not detail:
            detail = "本局信息不足，建议继续观察多场数据。"
        return LLMResult(title=title[:24], detail=detail[:1000])

    async def _call_llm(self, user_prompt: str) -> str:
        api_type = self._normalize_api_type(self.api_type, self.api_url)
        if api_type == "gemini":
            return await self._call_gemini(user_prompt)
        if api_type == "anthropic":
            return await self._call_anthropic(user_prompt)
        return await self._call_openai(user_prompt)

    async def _call_openai(self, user_prompt: str) -> str:
        url = self.api_url if self.api_url.endswith("/chat/completions") else f"{self.api_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.6,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    async def _call_gemini(self, user_prompt: str) -> str:
        api_url = self.api_url
        if "generateContent" not in api_url:
            if not api_url.endswith("/"):
                api_url += "/"
            if "models/" not in api_url:
                api_url += f"v1beta/models/{self.model}:generateContent"
            else:
                api_url += ":generateContent"
        connector = "&" if "?" in api_url else "?"
        if "key=" not in api_url:
            api_url = f"{api_url}{connector}key={self.api_key}"

        payload = {
            "contents": [
                {
                    "parts": [{"text": self.system_prompt + "\n\n" + user_prompt}]
                }
            ],
            "generationConfig": {"temperature": 0.6}
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(api_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return ""

    async def _call_anthropic(self, user_prompt: str) -> str:
        url = self.api_url if self.api_url.endswith("/messages") else f"{self.api_url}/messages"
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0.6,
            "system": self.system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("content", [])
        texts = [str(x.get("text", "")) for x in content if isinstance(x, dict)]
        return "\n".join([x for x in texts if x]).strip()

    @staticmethod
    def _normalize_api_type(api_type: str, api_url: str) -> str:
        v = (api_type or "openai").strip().lower()
        if v in {"openai", "gemini", "anthropic"}:
            return v
        url = (api_url or "").lower()
        if "generativelanguage.googleapis.com" in url or "gemini" in url:
            return "gemini"
        if "anthropic.com" in url:
            return "anthropic"
        return "openai"

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        if not text:
            return None
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None

    @staticmethod
    def _fallback(text: str) -> LLMResult:
        cleaned = (text or "").strip().replace("\n", " ")
        if not cleaned:
            return LLMResult(
                title="评价暂不可用",
                detail="模型返回异常，本次先展示战绩数据。",
            )
        title = cleaned[:14]
        if len(title) < 6:
            title = "风格概览"
        return LLMResult(title=title, detail=cleaned[:220])
