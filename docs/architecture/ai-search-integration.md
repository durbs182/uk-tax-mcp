# Azure AI Search Integration

## Purpose

Augment the HMRC Tax MCP server with retrieval-augmented generation (RAG) over
indexed HMRC manuals, Gov.uk guidance, and relevant legislation. When a rule
is executed or explained, the MCP server can return not just the computed result
but the actual HMRC source text that justifies it.

This transforms the MCP server from a **calculator** into a **grounded
knowledge source** — critical for a regulated financial planning context where
explanations must be traceable to authoritative HMRC material.

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════╗
║ OFFLINE — Indexing Pipeline                                      ║
║                                                                  ║
║  Sources                Chunker            Azure AI Search       ║
║  ─────────              ───────            ────────────────       ║
║  HMRC PTM manuals   ──► Split into    ──► Index: hmrc-guidance   ║
║  HMRC IHTM manuals      ~500-token         (text + vectors)      ║
║  HMRC CGT manual        chunks with                              ║
║  Gov.uk guidance        metadata       Azure OpenAI              ║
║  Legislation excerpts   + rule tags ──► text-embedding-3-small   ║
║                                         (generates vectors)      ║
╚══════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════╗
║ RUNTIME — Per Request                                            ║
║                                                                  ║
║  Later Life Planner (Next.js / ACA)                             ║
║      │                                                           ║
║      │  1. execute_rule(rule_id, inputs)                         ║
║      │  2. search_hmrc_guidance(query, rule_ids=[rule_id])       ║
║      ▼                                                           ║
║  HMRC Tax MCP Server (ACA)                                       ║
║      │                                                           ║
║      ├──► Rule Registry  ──► tax calculation result              ║
║      │                                                           ║
║      └──► Azure AI Search                                        ║
║               hybrid search (keyword + vector)                   ║
║               semantic re-rank                                   ║
║               filter by rule_ids tag                             ║
║               returns: top-k {content, title, url, ref}          ║
║                                                                  ║
║  /api/optimizer-explain (Next.js API route)                      ║
║      │                                                           ║
║      │  prompt = rule result + search chunks                     ║
║      ▼                                                           ║
║  Azure OpenAI GPT-4o-mini                                        ║
║      streamed plain-English explanation with HMRC citations      ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## Index Schema

```json
{
  "name": "hmrc-guidance",
  "fields": [
    {
      "name": "id",
      "type": "Edm.String",
      "key": true,
      "filterable": true
    },
    {
      "name": "content",
      "type": "Edm.String",
      "searchable": true,
      "analyzer": "en.microsoft"
    },
    {
      "name": "title",
      "type": "Edm.String",
      "searchable": true,
      "retrievable": true
    },
    {
      "name": "source_url",
      "type": "Edm.String",
      "retrievable": true
    },
    {
      "name": "manual_ref",
      "type": "Edm.String",
      "filterable": true,
      "retrievable": true,
      "comment": "e.g. PTM063300, IHTM04261, CG64200"
    },
    {
      "name": "rule_ids",
      "type": "Collection(Edm.String)",
      "filterable": true,
      "retrievable": true,
      "comment": "rule IDs this chunk is relevant to, e.g. iht_nil_rate_band"
    },
    {
      "name": "topic_tags",
      "type": "Collection(Edm.String)",
      "filterable": true,
      "retrievable": true,
      "comment": "broad topics: pension, iht, cgt, sdlt, income_tax, care"
    },
    {
      "name": "source_type",
      "type": "Edm.String",
      "filterable": true,
      "comment": "manual | legislation | guidance | helpsheet"
    },
    {
      "name": "effective_from",
      "type": "Edm.String",
      "filterable": true,
      "comment": "tax year this content applies from, e.g. 2025-26"
    },
    {
      "name": "embedding",
      "type": "Collection(Edm.Single)",
      "dimensions": 1536,
      "vectorSearchProfile": "hmrc-vector-profile"
    }
  ],
  "vectorSearch": {
    "profiles": [
      {
        "name": "hmrc-vector-profile",
        "algorithm": "hmrc-hnsw"
      }
    ],
    "algorithms": [
      {
        "name": "hmrc-hnsw",
        "kind": "hnsw",
        "hnswParameters": { "metric": "cosine", "m": 4 }
      }
    ]
  },
  "semantic": {
    "configurations": [
      {
        "name": "hmrc-semantic-config",
        "prioritizedFields": {
          "titleField": { "fieldName": "title" },
          "contentFields": [{ "fieldName": "content" }]
        }
      }
    ]
  }
}
```

---

## Source Material & Chunking Strategy

| Source | Volume | Rule tag scope | Chunk size |
|---|---|---|---|
| HMRC Pensions Tax Manual (PTM) | ~2,000 sections | `pension_*`, `mpaa_*`, `lsa_*` | 500 tokens |
| HMRC IHT Manual (IHTM) | ~1,500 sections | `iht_*` | 500 tokens |
| HMRC Capital Gains Manual | ~800 sections | `cgt_*`, `prr_*`, `gia_*` | 500 tokens |
| HMRC Property Income Manual | ~400 sections | `property_income_bands` | 500 tokens |
| HMRC Employment Income Manual | ~200 relevant sections | `pension_net_pay_*` | 500 tokens |
| Gov.uk pension guidance pages | ~150 pages | `pension_*`, `state_pension_*` | 300 tokens |
| Gov.uk IHT guidance pages | ~80 pages | `iht_*` | 300 tokens |
| HMRC helpsheets (HS) | ~30 sheets | mixed | 300 tokens |
| Key legislation excerpts | selected sections | rule-specific | 400 tokens |

**Chunking rules:**
- Split on section headings first; fall back to paragraph breaks
- Preserve the manual reference (e.g. `PTM063300`) in every chunk's metadata
- Overlap: 50 tokens between adjacent chunks from the same section
- Never split a statutory definition across two chunks

---

## Indexing Pipeline

```
scripts/
└── indexing/
    ├── fetch_hmrc_sources.py      # download / scrape source material
    ├── chunk_documents.py         # split into chunks with metadata
    ├── embed_chunks.py            # call Azure OpenAI embeddings API
    ├── upload_to_search.py        # batch upload to AI Search index
    └── tag_rules.py               # map chunks → rule_ids using citations
```

### Step 1 — Fetch Sources

```python
# scripts/indexing/fetch_hmrc_sources.py
"""
Fetches HMRC manual sections from Gov.uk. The manuals are published as
structured HTML at predictable URLs; each section maps to one document chunk.

Outputs: data/raw/{manual_name}/{section_ref}.json
  {
    "ref": "PTM063300",
    "title": "Serious ill-health lump sum",
    "content": "...",
    "source_url": "https://www.gov.uk/hmrc-internal-manuals/...",
    "manual": "PTM",
    "topic_tags": ["pension"],
    "rule_ids": ["pension_serious_ill_health_lump_sum"]
  }
"""
import httpx
from bs4 import BeautifulSoup
from pathlib import Path
import json

MANUALS = {
    "PTM": "https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual",
    "IHTM": "https://www.gov.uk/hmrc-internal-manuals/inheritance-tax-manual",
    "CG": "https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual",
    "PIM": "https://www.gov.uk/hmrc-internal-manuals/property-income-manual",
}

# Manual ref → rule_id mapping (maintained alongside rule YAML files)
CITATION_MAP: dict[str, list[str]] = {
    "PTM063300": ["pension_serious_ill_health_lump_sum"],
    "PTM073000": ["pension_death_benefit_lump_sum"],
    "IHTM04261": ["iht_nil_rate_band"],
    "IHTM46000": ["iht_residence_nil_rate_band", "iht_rnrb_taper"],
    # ... extended from rule YAML citations fields
}
```

### Step 2 — Chunk and Embed

```python
# scripts/indexing/chunk_documents.py + embed_chunks.py
from openai import AzureOpenAI
import tiktoken

CHUNK_SIZE = 500       # tokens
CHUNK_OVERLAP = 50     # tokens

enc = tiktoken.encoding_for_model("text-embedding-3-small")
client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_KEY"],
    api_version="2024-02-01",
)

def embed(text: str) -> list[float]:
    response = client.embeddings.create(
        input=text,
        model="text-embedding-3-small",   # 1536-dim, cheap
    )
    return response.data[0].embedding

def chunk_document(doc: dict) -> list[dict]:
    tokens = enc.encode(doc["content"])
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE, len(tokens))
        chunk_text = enc.decode(tokens[start:end])
        chunks.append({
            "id": f"{doc['ref']}-{start}",
            "content": chunk_text,
            "title": doc["title"],
            "source_url": doc["source_url"],
            "manual_ref": doc["ref"],
            "rule_ids": doc.get("rule_ids", []),
            "topic_tags": doc.get("topic_tags", []),
            "source_type": doc.get("source_type", "manual"),
            "effective_from": doc.get("effective_from", "2025-26"),
            "embedding": embed(chunk_text),
        })
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks
```

### Step 3 — Upload to AI Search

```python
# scripts/indexing/upload_to_search.py
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

client = SearchClient(
    endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
    index_name="hmrc-guidance",
    credential=AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]),
)

def upload_batch(chunks: list[dict], batch_size: int = 100) -> None:
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        result = client.upload_documents(documents=batch)
        failed = [r for r in result if not r.succeeded]
        if failed:
            raise RuntimeError(f"{len(failed)} chunks failed to upload")
```

---

## New MCP Tool

```python
# src/hmrc_tax_mcp/tools/search_guidance.py
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery
from azure.core.credentials import AzureKeyCredential
import os

_client: SearchClient | None = None


def _get_client() -> SearchClient:
    global _client
    if _client is None:
        _client = SearchClient(
            endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
            index_name="hmrc-guidance",
            credential=AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]),
        )
    return _client


def search_hmrc_guidance(
    query: str,
    rule_ids: list[str] | None = None,
    topic_tags: list[str] | None = None,
    top: int = 5,
) -> list[dict]:
    """
    Hybrid search (keyword + vector) over indexed HMRC manuals and guidance.

    Args:
        query:       Plain-English question or topic to search for.
        rule_ids:    Narrow results to chunks tagged with these rule IDs.
        topic_tags:  Narrow results to a topic area (pension, iht, cgt, …).
        top:         Maximum number of chunks to return.

    Returns:
        Ranked list of passages, each with content, title, source_url,
        manual_ref, and a relevance score.
    """
    filters = []
    if rule_ids:
        tags = " or ".join(f"rule_ids/any(r: r eq '{rid}')" for rid in rule_ids)
        filters.append(f"({tags})")
    if topic_tags:
        topics = " or ".join(
            f"topic_tags/any(t: t eq '{tag}')" for tag in topic_tags
        )
        filters.append(f"({topics})")

    results = _get_client().search(
        search_text=query,
        vector_queries=[
            VectorizableTextQuery(
                text=query,
                k_nearest_neighbors=top,
                fields="embedding",
            )
        ],
        filter=" and ".join(filters) if filters else None,
        select=["content", "title", "source_url", "manual_ref", "rule_ids"],
        top=top,
        query_type="semantic",
        semantic_configuration_name="hmrc-semantic-config",
    )

    return [
        {
            "content": r["content"],
            "title": r["title"],
            "source_url": r["source_url"],
            "manual_ref": r["manual_ref"],
            "rule_ids": r["rule_ids"],
            "score": r["@search.reranker_score"],
        }
        for r in results
    ]
```

### MCP Registration (server.py)

```python
@mcp.tool()
def search_hmrc_guidance_tool(
    query: str,
    rule_ids: list[str] | None = None,
    topic_tags: list[str] | None = None,
    top: int = 5,
) -> list[dict]:
    """
    Search HMRC manuals, Gov.uk guidance, and legislation for a
    plain-English query. Optionally scope to chunks relevant to
    specific rule IDs or topic areas (pension, iht, cgt, sdlt, care).
    Returns ranked passages with source citations.
    """
    from hmrc_tax_mcp.tools.search_guidance import search_hmrc_guidance
    return search_hmrc_guidance(query, rule_ids=rule_ids,
                                topic_tags=topic_tags, top=top)
```

---

## How Next.js Uses It

```ts
// /api/optimizer-explain/route.ts
export async function POST(req: Request) {
  const { ruleId, inputs, userQuestion } = await req.json();

  // Parallel: execute rule + retrieve grounded guidance
  const [taxResult, guidance] = await Promise.all([
    mcpClient.callTool('execute_rule', { rule_id: ruleId, inputs }),
    mcpClient.callTool('search_hmrc_guidance_tool', {
      query: userQuestion,
      rule_ids: [ruleId],
      top: 4,
    }),
  ]);

  // Stream grounded explanation
  return streamText({
    model: azure('gpt-4-1-mini'),
    system: SYSTEM_PROMPT,
    messages: [{ role: 'user', content: buildPrompt(userQuestion, taxResult, guidance) }],
  }).toDataStreamResponse();
}

function buildPrompt(
  question: string,
  taxResult: unknown,
  guidance: GuidanceChunk[],
): string {
  const citations = guidance
    .map(g => `**[${g.manual_ref}](${g.source_url}) — ${g.title}**\n${g.content}`)
    .join('\n\n---\n\n');

  return `
Question: ${question}

Tax calculation result (from HMRC rule engine):
${JSON.stringify(taxResult, null, 2)}

Relevant HMRC guidance:
${citations}

Explain the tax result in plain English for a non-specialist. Cite the HMRC
sources above. Do not introduce information not present in the guidance or the
calculation result.
  `.trim();
}
```

---

## Keeping the Index Fresh

| Trigger | Action |
|---|---|
| New tax year (April) | Re-run full pipeline for updated manuals |
| HMRC manual section change | Webhook or scheduled nightly diff; re-index changed sections only |
| New rule added to registry | `tag_rules.py` run to map citations → rule_ids; partial re-index |
| Legislative change mid-year | Manual trigger via `scripts/indexing/upload_to_search.py --ref IHTM46000` |

A GitHub Actions scheduled workflow (`0 2 * * *`) runs a diff check against
the Gov.uk manuals and triggers a targeted re-index when content changes.

---

## Infrastructure

```yaml
# Bicep / Azure resource additions required
resources:
  - type: Microsoft.Search/searchServices
    name: hmrc-tax-search
    sku: standard          # required for semantic search
    location: uksouth
    properties:
      replicaCount: 1
      partitionCount: 1    # ~2GB index capacity; increase if needed

  # Azure OpenAI already in stack — add embedding deployment
  - type: Microsoft.CognitiveServices/accounts/deployments
    name: text-embedding-3-small
    model: text-embedding-3-small
    capacity: 10           # 10K tokens/min; sufficient for batch indexing
```

**Estimated cost (UK South, 2025 pricing):**

| Resource | Tier | Est. monthly cost |
|---|---|---|
| AI Search | Standard S1 | ~£250/month |
| Embedding (indexing, one-off) | 2M tokens | ~£0.04 |
| Embedding (runtime, per query) | ~50K tokens/day | ~£0.03/day |
| Semantic ranker calls | ~10K/day | included in Standard |

Total AI Search addition: **~£250/month**. Runtime embedding cost is negligible.

---

## Environment Variables Required

```bash
# Added to ACA environment / Key Vault
AZURE_SEARCH_ENDPOINT=https://hmrc-tax-search.search.windows.net
AZURE_SEARCH_KEY=<managed-identity-or-key>
AZURE_SEARCH_INDEX=hmrc-guidance

# Already in stack for OpenAI — also used for embeddings
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_KEY=<key>
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
```

**Preferred:** Replace key-based auth with **Managed Identity** for both AI
Search and Azure OpenAI — the ACA container identity is granted
`Search Index Data Reader` and `Cognitive Services OpenAI User` roles,
removing all secrets from the environment entirely.

---

## Related Documents

- [`docs/architecture/`](./) — architecture overview
- [`docs/rules/retirement-later-life-plan.md`](../rules/retirement-later-life-plan.md) — rule set
- [`docs/integration/later-life-planner.md`](../integration/later-life-planner.md) — app integration
- [`scripts/indexing/`](../../scripts/indexing/) — indexing pipeline scripts
