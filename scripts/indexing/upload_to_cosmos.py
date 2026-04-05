"""
Upload chunked HMRC documents (with embeddings) to Azure Cosmos DB for NoSQL.

Reads from data/chunked/{manual}/{chunk_id}.json and upserts into the
hmrc-chunks container.

Authentication priority:
  1. COSMOS_KEY env var — account master key, used by GitHub Actions where
     Cosmos DB native RBAC (AAD data-plane) cannot authorise write operations
     through the OIDC-federated managed identity credential.
  2. DefaultAzureCredential — used in ACA (Managed Identity via IMDS) and
     local development (az login).

Usage:
    python upload_to_cosmos.py [--manual PTM] [--ref PTM063300] [--dry-run]

Environment variables:
    COSMOS_URL         Cosmos DB account endpoint (required)
    COSMOS_DB_NAME     Database name (default: hmrc-guidance)
    COSMOS_CONTAINER   Container name (default: hmrc-chunks)
    COSMOS_KEY         Account master key (optional — falls back to DefaultAzureCredential)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.identity import DefaultAzureCredential

CHUNKED_DIR = Path("data/chunked")
BATCH_SIZE = 50          # Cosmos DB upserts per loop iteration
VECTOR_DIMENSIONS = 3072  # text-embedding-3-large


def _cosmos_client(cosmos_url: str) -> CosmosClient:
    """
    Build a CosmosClient using the account key when COSMOS_KEY is set,
    otherwise fall back to DefaultAzureCredential.

    Key auth is required for GitHub Actions: the OIDC-federated managed
    identity credential is consistently rejected by Cosmos DB's native RBAC
    for data-plane writes even when the correct SQL role is assigned.  In ACA
    the IMDS-backed ManagedIdentityCredential resolves correctly.
    """
    cosmos_key = os.environ.get("COSMOS_KEY")
    if cosmos_key:
        return CosmosClient(url=cosmos_url, credential=cosmos_key)
    return CosmosClient(url=cosmos_url, credential=DefaultAzureCredential())


def get_cosmos_container():
    """Return the Cosmos DB container client."""
    cosmos_url = os.environ["COSMOS_URL"]
    db_name = os.environ.get("COSMOS_DB_NAME", "hmrc-guidance")
    container_name = os.environ.get("COSMOS_CONTAINER", "hmrc-chunks")

    client = _cosmos_client(cosmos_url)
    db = client.get_database_client(db_name)
    return db.get_container_client(container_name)


def ensure_container_exists() -> None:
    """
    Create the container with the vector policy if it does not already exist.
    Safe to call on every run — create_container_if_not_exists is idempotent.

    The database must already exist.  The Cosmos DB Built-in Data Contributor
    role (00000000-0000-0000-0000-000000000002) does not include
    Microsoft.DocumentDB/databaseAccounts/sqlDatabases/write, so database
    creation from the data plane is not permitted.  Create the database once
    via the management plane:
        az cosmosdb sql database create --account-name <acct> \
            --resource-group <rg> --name <db>

    NOTE: Vector indexing policy can only be set at container creation time.
    If the container already exists without a vector policy you must delete
    and recreate it.
    """
    cosmos_url = os.environ["COSMOS_URL"]
    db_name = os.environ.get("COSMOS_DB_NAME", "hmrc-guidance")
    container_name = os.environ.get("COSMOS_CONTAINER", "hmrc-chunks")

    client = _cosmos_client(cosmos_url)
    db = client.get_database_client(db_name)

    db.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/manual_ref"),
        # Vector embedding policy — must be set at creation
        vector_embedding_policy={
            "vectorEmbeddings": [
                {
                    "path": "/embedding",
                    "dataType": "float32",
                    "dimensions": VECTOR_DIMENSIONS,
                    "distanceFunction": "cosine",
                }
            ]
        },
        # Indexing policy — DiskANN for scalable ANN; use "flat" for <10K docs
        indexing_policy={
            "vectorIndexes": [
                {"path": "/embedding", "type": "diskANN"}
            ]
        },
    )
    print(f"Container '{container_name}' ready in database '{db_name}'.")


def upsert_chunks(chunks: list[dict], container, dry_run: bool = False) -> None:
    """Upsert a list of chunk dicts into the container."""
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        if dry_run:
            print(f"  [dry-run] would upsert {len(batch)} chunks")
            continue
        for chunk in batch:
            try:
                container.upsert_item(chunk)
            except exceptions.CosmosHttpResponseError as exc:
                raise RuntimeError(
                    f"Failed to upsert chunk {chunk.get('id')}: {exc}"
                ) from exc
        print(f"  Upserted chunks {i + 1}–{min(i + BATCH_SIZE, len(chunks))}")


def upload_manual(
    manual_name: str,
    ref_filter: str | None,
    container,
    dry_run: bool = False,
) -> int:
    """Upload all chunks for a manual, optionally filtered to one ref."""
    chunked_dir = CHUNKED_DIR / manual_name
    if not chunked_dir.exists():
        print(f"  No chunked data for {manual_name} — run chunk_and_embed.py first.")
        return 0

    paths = list(chunked_dir.glob("*.json"))
    if ref_filter:
        paths = [p for p in paths if p.name.startswith(ref_filter.upper())]

    chunks = [json.loads(p.read_text()) for p in paths]
    if not chunks:
        print(f"  No chunks to upload for {manual_name} {ref_filter or ''}.")
        return 0

    print(f"Uploading {len(chunks)} chunks for {manual_name} …")
    upsert_chunks(chunks, container, dry_run=dry_run)
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", help="Upload one manual only (e.g. PTM)")
    parser.add_argument("--ref", help="Upload one section only (e.g. PTM063300)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without writing to Cosmos DB",
    )
    parser.add_argument(
        "--skip-ensure-container",
        action="store_true",
        help="Skip container creation check (use when container already exists)",
    )
    args = parser.parse_args()

    if not args.skip_ensure_container:
        ensure_container_exists()

    container = get_cosmos_container()

    if args.ref:
        manual = "".join(c for c in args.ref if c.isalpha()).upper()
        upload_manual(manual, args.ref, container, dry_run=args.dry_run)
    elif args.manual:
        upload_manual(args.manual, None, container, dry_run=args.dry_run)
    else:
        manuals = [d.name for d in CHUNKED_DIR.iterdir() if d.is_dir()]
        total = 0
        for manual in sorted(manuals):
            total += upload_manual(manual, None, container, dry_run=args.dry_run)
        print(f"\nDone. Total upserted: {total} chunks.")


if __name__ == "__main__":
    main()
