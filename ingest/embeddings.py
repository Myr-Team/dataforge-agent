import os
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI


DEFAULT_DIMENSIONS = 1536


def embedding_dimensions() -> int:
    try:
        return int(os.environ.get("DF_EMBEDDING_DIMENSIONS", DEFAULT_DIMENSIONS))
    except ValueError:
        return DEFAULT_DIMENSIONS


def embed_texts(texts: list[str], *, batch_size: int = 16) -> list[list[float]]:
    if not texts:
        return []
    deployment = os.environ.get("DF_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    openai_client = _embedding_client()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = [text[:8000] for text in texts[start : start + batch_size]]
        try:
            response = openai_client.embeddings.create(model=deployment, input=batch)
        except Exception:
            response = _azure_openai_client().embeddings.create(model=deployment, input=batch)
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors.extend([list(map(float, item.embedding)) for item in ordered])
    return vectors


def try_embed_texts(texts: list[str]) -> list[list[float] | None]:
    try:
        return embed_texts(texts)
    except Exception:
        return [None for _ in texts]


def enrich_documents_with_embeddings(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    texts = [_embedding_text(doc) for doc in docs]
    vectors = try_embed_texts(texts)
    enriched: list[dict[str, Any]] = []
    for doc, vector in zip(docs, vectors):
        item = dict(doc)
        if vector and len(vector) == embedding_dimensions():
            item["content_vector"] = vector
        enriched.append(item)
    return enriched


def _embedding_text(doc: dict[str, Any]) -> str:
    title = str(doc.get("title") or "")
    content = str(doc.get("content") or "")
    source = str(doc.get("source_file") or "")
    return "\n".join(part for part in (title, source, content) if part)


def _embedding_client() -> Any:
    if os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return _azure_openai_client()
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return _azure_openai_client()
    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True)
    return client.get_openai_client()


def _azure_openai_client() -> AzureOpenAI:
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Missing OPENAI_ENDPOINT for Azure OpenAI embeddings")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        return AzureOpenAI(
            azure_endpoint=endpoint.rstrip("/"),
            api_key=api_key,
            api_version=os.environ.get("OPENAI_API_VERSION", "2024-10-21"),
        )
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=endpoint.rstrip("/"),
        azure_ad_token_provider=token_provider,
        api_version=os.environ.get("OPENAI_API_VERSION", "2024-10-21"),
    )
