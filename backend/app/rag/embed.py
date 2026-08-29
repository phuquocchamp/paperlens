"""Dense + sparse embedding with a Redis cache (Build Sheet §5.3).

Dense: OpenAI ``text-embedding-3-large`` with ``dimensions=1024`` passed in the
API call (no manual truncation — that would introduce norm bias). Results are
cached in Redis under ``emb:{model}:{dims}:{sha256(embed_text)}`` and the cache is
written after EACH sub-batch (so a crash mid-document still banks the work). fp16
storage is safe (build sheet measured top-10 recall 1.0000).

Sparse: BM25 via FastEmbed (``Qdrant/bm25``) — client-side term frequencies. The
collection's ``Modifier.IDF`` computes IDF server-side, so forgetting it is the
classic silent bug; we never pass a hard-coded ``avg_len`` (config keeps it
``None`` on purpose — it is measured and reported, not defaulted to 256).

Heavy imports (openai, fastembed, numpy) are lazy so the module stays import-cheap.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.config import settings
from app.rag.errors import EmbeddingError

# text-embedding-3-large list price (USD / 1M tokens), for cost reporting only.
EMBED_PRICE_PER_1M_USD = 0.13
_SUB_BATCH = 96  # inputs per OpenAI request; also the cache-write cadence.


def cache_key(embed_text: str) -> str:
    digest = hashlib.sha256(embed_text.encode("utf-8")).hexdigest()
    return f"emb:{settings.embedding_model}:{settings.embedding_dims}:{digest}"


@dataclass
class EmbedResult:
    """Dense + sparse vectors aligned to the input order, plus usage stats."""

    dense: list[list[float]]
    sparse: list[tuple[list[int], list[float]]]
    billed_tokens: int = 0
    cached_count: int = 0
    billed_count: int = 0
    corpus_token_counts: list[int] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return self.billed_tokens / 1_000_000 * EMBED_PRICE_PER_1M_USD


class Embedder:
    """Owns the OpenAI client, the Redis cache and the FastEmbed BM25 model."""

    def __init__(self) -> None:
        self._openai = None
        self._redis = None
        self._bm25 = None

    # -- lazy singletons --------------------------------------------------- #
    def _client(self):
        if self._openai is None:
            from openai import AsyncOpenAI

            if not settings.openai_api_key:
                raise EmbeddingError("OPENAI_API_KEY is not set")
            self._openai = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._openai

    async def _redis_client(self):
        if self._redis is None:
            import redis.asyncio as redis

            self._redis = redis.from_url(settings.redis_url, decode_responses=False)
        return self._redis

    def _sparse_model(self):
        if self._bm25 is None:
            from fastembed import SparseTextEmbedding

            self._bm25 = SparseTextEmbedding(model_name="Qdrant/bm25")
        return self._bm25

    async def aclose(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # pragma: no cover
                pass

    # -- dense with cache -------------------------------------------------- #
    async def embed_dense(self, texts: list[str]) -> tuple[
        list[list[float]], int, int, int
    ]:
        """Return (vectors, billed_tokens, cached_count, billed_count)."""
        import numpy as np

        r = await self._redis_client()
        keys = [cache_key(t) for t in texts]

        vectors: list[list[float] | None] = [None] * len(texts)
        cached = 0
        try:
            raw = await r.mget(keys)
        except Exception:  # cache is best-effort; a Redis blip must not fail ingest
            raw = [None] * len(texts)
        for i, blob in enumerate(raw):
            if blob:
                vectors[i] = np.frombuffer(blob, dtype=np.float16).astype(
                    np.float32
                ).tolist()
                cached += 1

        misses = [i for i, v in enumerate(vectors) if v is None]
        billed_tokens = 0
        billed_count = 0
        client = self._client() if misses else None

        for start in range(0, len(misses), _SUB_BATCH):
            sub = misses[start : start + _SUB_BATCH]
            inputs = [texts[i] for i in sub]
            try:
                resp = await client.embeddings.create(
                    model=settings.embedding_model,
                    input=inputs,
                    dimensions=settings.embedding_dims,
                )
            except Exception as exc:  # transient provider failure -> FAILED
                raise EmbeddingError(f"embedding request failed: {exc}") from exc

            billed_tokens += getattr(resp.usage, "total_tokens", 0) or 0
            billed_count += len(sub)

            writes: dict[bytes | str, bytes] = {}
            for idx, data in zip(sub, resp.data, strict=True):
                vec = data.embedding
                vectors[idx] = vec
                writes[keys[idx]] = np.asarray(vec, dtype=np.float16).tobytes()
            # Write cache after EVERY sub-batch (bank partial progress).
            try:
                await r.mset(writes)
            except Exception:  # pragma: no cover - cache write is best-effort
                pass

        return [v for v in vectors], billed_tokens, cached, billed_count  # type: ignore[misc]

    # -- sparse (no cache; client-side TF) --------------------------------- #
    def embed_sparse(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        model = self._sparse_model()
        out: list[tuple[list[int], list[float]]] = []
        for emb in model.embed(texts):
            out.append(
                ([int(i) for i in emb.indices], [float(v) for v in emb.values])
            )
        return out

    async def embed(self, texts: list[str]) -> EmbedResult:
        """Embed a list of ``embed_text`` strings (dense + sparse)."""
        if not texts:
            return EmbedResult(dense=[], sparse=[])
        dense, billed_tokens, cached, billed = await self.embed_dense(texts)
        sparse = self.embed_sparse(texts)
        return EmbedResult(
            dense=dense,
            sparse=sparse,
            billed_tokens=billed_tokens,
            cached_count=cached,
            billed_count=billed,
        )


__all__ = ["Embedder", "EmbedResult", "cache_key", "EMBED_PRICE_PER_1M_USD"]
