"""
Chunk raw HMRC documents and generate vector embeddings via Azure OpenAI.

Reads from data/raw/{manual}/{ref}.json and writes to
data/chunked/{manual}/{ref}-{offset}.json.

Usage:
    python chunk_and_embed.py [--manual PTM] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import tiktoken
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

RAW_DIR = Path("data/raw")
CHUNKED_DIR = Path("data/chunked")

CHUNK_SIZE = 500        # tokens
CHUNK_OVERLAP = 50      # tokens
EMBEDDING_MODEL = "text-embedding-3-large"
EMBED_BATCH_SIZE = 16   # chunks per embedding API call


def get_openai_client() -> AzureOpenAI:
    """
    Build an AzureOpenAI client using DefaultAzureCredential.

    In GitHub Actions (OIDC) and ACA (Managed Identity) the credential is
    resolved automatically.  Locally, ``az login`` provides the credential.
    The AZURE_OPENAI_KEY environment variable is intentionally not used so
    that no long-lived secrets are required.
    """
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        azure_ad_token_provider=token_provider,
        api_version="2024-02-01",
    )


def chunk_text(text: str, enc: tiktoken.Encoding) -> list[tuple[int, str]]:
    """
    Split text into overlapping token chunks.
    Returns list of (start_token_offset, chunk_text) pairs.
    """
    tokens = enc.encode(text)
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append((start, enc.decode(chunk_tokens)))
        if end == len(tokens):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def embed_batch(texts: list[str], client: AzureOpenAI) -> list[list[float]]:
    """Embed a batch of texts, returning one embedding vector per text."""
    response = client.embeddings.create(
        input=texts,
        model=EMBEDDING_MODEL,
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


def process_document(doc: dict, enc: tiktoken.Encoding, client: AzureOpenAI | None) -> list[dict]:
    """Chunk a document and optionally embed each chunk."""
    raw_chunks = chunk_text(doc["content"], enc)
    chunk_dicts = []

    # Build chunk dicts without embeddings first
    for offset, text in raw_chunks:
        chunk_id = f"{doc['ref']}-{offset}"
        chunk_dicts.append({
            "id": chunk_id,
            "content": text,
            "title": doc["title"],
            "source_url": doc["source_url"],
            "manual_ref": doc["ref"],
            "rule_ids": doc.get("rule_ids", []),
            "topic_tags": doc.get("topic_tags", []),
            "source_type": doc.get("source_type", "manual"),
            "effective_from": doc.get("effective_from", "2025-26"),
            "embedding": [],
        })

    # Embed in batches
    if client is not None:
        texts = [c["content"] for c in chunk_dicts]
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            embeddings = embed_batch(batch, client)
            for j, emb in enumerate(embeddings):
                chunk_dicts[i + j]["embedding"] = emb
            time.sleep(0.1)  # stay under rate limit

    return chunk_dicts


def process_manual(manual_name: str, dry_run: bool = False) -> int:
    """Process all raw documents for a manual. Returns chunk count."""
    raw_dir = RAW_DIR / manual_name
    if not raw_dir.exists():
        print(f"  No raw data for {manual_name} — run fetch_hmrc_sources.py first.")
        return 0

    out_dir = CHUNKED_DIR / manual_name
    out_dir.mkdir(parents=True, exist_ok=True)

    enc = tiktoken.encoding_for_model("text-embedding-3-small")
    client = None if dry_run else get_openai_client()

    docs = list(raw_dir.glob("*.json"))
    print(f"Processing {len(docs)} documents for {manual_name} …")
    total_chunks = 0

    for doc_path in docs:
        doc = json.loads(doc_path.read_text())
        chunks = process_document(doc, enc, client)

        for chunk in chunks:
            dest = out_dir / f"{chunk['id']}.json"
            if not dry_run:
                dest.write_text(json.dumps(chunk, ensure_ascii=False))

        total_chunks += len(chunks)

    print(f"  {manual_name}: {len(docs)} docs → {total_chunks} chunks")
    return total_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", help="Process one manual only (e.g. PTM)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Chunk only, skip embeddings")
    args = parser.parse_args()

    if args.manual:
        manuals = [args.manual]
    else:
        manuals = [d.name for d in RAW_DIR.iterdir() if d.is_dir()]

    total = 0
    for manual in manuals:
        total += process_manual(manual, dry_run=args.dry_run)

    print(f"\nDone. Total chunks: {total}")


if __name__ == "__main__":
    main()
