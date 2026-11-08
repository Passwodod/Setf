"""OpenAI provider (Chat Completions with strict JSON-schema outputs + Embeddings) over plain httpx.
Needs SIA_OPENAI_API_KEY. Unit tests cover the request/response handling with a stubbed transport only;
behaviour against the live API is checked by the opt-in `real_provider` test."""
import json
import logging

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.providers.base import ProviderError, ProviderNotConfigured, T

log = logging.getLogger("sia.ai.openai")
EMBED_BATCH = 96


def strictify(schema: dict) -> dict:
    """OpenAI strict mode: every object lists all properties as required and forbids extras; no defaults."""
    def walk(node):
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)
            if node.get("type") == "object" or "properties" in node:
                props = node.setdefault("properties", {})
                node["required"] = list(props)
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    out = json.loads(json.dumps(schema))
    walk(out)
    return out


class OpenAIProvider:
    name = "openai"
    is_mock = False
    embedding_dim = 1536

    def __init__(self, api_key: str, model: str, embedding_model: str, base_url: str = "https://api.openai.com/v1",
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 60.0):
        if not api_key:
            raise ProviderNotConfigured("SIA_OPENAI_API_KEY is not set")
        self.model, self.embedding_model = model, embedding_model
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                         headers={"Authorization": f"Bearer {api_key}"})

    async def _post(self, path: str, body: dict) -> dict:
        try:
            r = await self._client.post(path, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"network error: {type(exc).__name__}", transient=True) from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderError(f"openai http {r.status_code}", transient=True)
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message", "")[:300]
            except Exception:
                detail = ""
            raise ProviderError(f"openai http {r.status_code}: {detail}", transient=False)
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError("openai returned invalid JSON body", transient=True) from exc

    async def structured(self, *, task: str, system: str, user: str, schema: type[T]) -> T:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": task, "strict": True, "schema": strictify(schema.model_json_schema())}},
        }
        data = await self._post("/chat/completions", body)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("unexpected response shape", transient=True) from exc
        if msg.get("refusal"):
            raise ProviderError("model refused the request", transient=False)
        try:
            return schema.model_validate_json(msg.get("content") or "")
        except ValidationError as exc:
            raise ProviderError(f"model output failed schema validation ({exc.error_count()} errors)", transient=True) from exc

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            data = await self._post("/embeddings", {"model": self.embedding_model, "input": texts[i:i + EMBED_BATCH]})
            try:
                rows = sorted(data["data"], key=lambda d: d["index"])
                vecs = [d["embedding"] for d in rows]
            except (KeyError, TypeError) as exc:
                raise ProviderError("unexpected embeddings response", transient=True) from exc
            if any(len(v) != self.embedding_dim for v in vecs):
                raise ProviderError("embedding dimension mismatch", transient=False)
            out.extend(vecs)
        return out

    async def aclose(self) -> None:
        await self._client.aclose()
