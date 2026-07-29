# Trendtype distributor directory
<img width="1914" height="926" alt="image" src="https://github.com/user-attachments/assets/3ab19f9e-81ba-4f2e-ad94-82eb483dd7c1" />

## 1. Project overview

Trendtype is a local distributor-directory tool. It ingests an inconsistent
JSON dataset into SQLite, exposes the normalized records through a FastAPI
search API, and serves a thin HTML, CSS, and JavaScript interface from the same
application.

## 2. Quick start

The only prerequisite is Docker with Docker Compose.

From the repository root, run:

```sh
docker compose up --build
```

The application is then available at:

- UI: <http://localhost:8000/>
- API: <http://localhost:8000/api/distributors>
- Swagger: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Startup creates the SQLite schema and reconciles the database from the supplied
`data/distributors.json` snapshot. Stop and remove the container with:

```sh
docker compose down
```

## 3. API usage

`GET /api/distributors` accepts these query parameters:

| Parameter | Meaning |
| --- | --- |
| `name` | Optional substring search over primary names and aliases |
| `country` | Optional normalized exact country match |
| `category` | Optional normalized exact category match |
| `page` | Page number; defaults to `1` and must be at least `1` |
| `page_size` | Results per page; defaults to `20` and must be from `1` to `100` |

Supplied filters are combined with `AND`. Filter text is normalized with
Unicode NFKC, whitespace collapse, and Unicode case folding. SQL wildcard
characters (`%`, `_`, and `\`) are treated literally. Results are ordered by
normalized distributor name and then source ID.

With no filters, the endpoint returns all distributors through pagination. No
matches and pages beyond the final page return HTTP 200 with an empty `items`
array. Invalid pagination or blank filters return HTTP 422. Unknown query
parameters return HTTP 400.

Example:

```sh
curl "http://localhost:8000/api/distributors?name=Premier&country=Rwanda&page=1&page_size=10"
```

Abridged response, with other item fields omitted:

```json
{
  "items": [
    {
      "id": 1,
      "name": "Premier Distribution",
      "country": "Rwanda",
      "categories": ["cosmetics", "personal care", "textiles"]
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 10,
    "total_items": 1,
    "total_pages": 1
  }
}
```

## 4. Schema design

The canonical schema is
[`database/schema.sql`](database/schema.sql). It contains seven application
tables:

| Table | Purpose |
| --- | --- |
| `distributors` | One row per supplied source record |
| `countries` | Canonical country dimension |
| `categories` | Canonical category dimension |
| `distributor_aliases` | Ordered one-to-many aliases |
| `distributor_categories` | Many-to-many distributor/category relationships |
| `distributor_locations` | Ordered one-to-many locations |
| `distributor_contacts` | One-to-many contacts, including their source shape |

`source_id` is the stable source identity exposed by the API; internal integer
IDs support foreign-key relationships. Countries and categories are
canonicalized through normalized names, while categories use a junction table.
Aliases, locations, and contacts remain child collections.

Nested contacts and flat contact fields are stored separately because source
record 17 contains conflicting values in those two representations. Exact
duplicate distributor records are also preserved: identity cannot safely be
inferred from names or contact details. `record_fingerprint` identifies
possible duplicate content but is deliberately indexed without a uniqueness
constraint.

`raw_json` preserves the full original source record in a deterministic JSON
serialization. Numeric founded-year strings become integers, while `unknown`
and null become SQL `NULL`. Blank descriptions and placeholders such as `N/A`
and `TBD` become `NULL`. Original date text is always retained, and only
unambiguous dates receive a parsed value.

Searchable fields have separate display values and normalized keys. Names,
aliases, country and category labels, and cities use NFKC-normalized,
whitespace-collapsed display values; their normalized columns also apply
Unicode case folding for comparisons. The raw source values remain available
in `raw_json`.

The supplied snapshot produces 56 distributors, 16 countries, 15 categories,
54 aliases, 75 category relationships, 67 locations, and 34 contacts.

## 5. Ingestion behaviour and assumptions

Initialization runs in the FastAPI lifespan startup handler. Schema creation
and the complete import share one transaction, and SQLite foreign-key
enforcement is enabled on every connection.

Source IDs drive distributor upserts. Each distributor's aliases, categories,
locations, and contacts are replaced from the current source record, so child
collections are reconciled as well. The JSON file is treated as an
authoritative full snapshot: distributors missing from it are deleted, then
unused country and category dimensions are removed.

Repeated imports converge on the same logical database state. A second
unchanged import still re-upserts every distributor and rebuilds its child
collections; idempotent describes the resulting data, not zero database
writes. Any malformed record aborts and rolls back the whole import rather than
leaving a partial update.

## 6. Trade-offs under time pressure

- SQLite with SQLAlchemy Core and parameterized raw SQL keeps local setup small.
  There are no cloud services, ORM models, or migrations.
- Rebuilding and reconciling from the authoritative JSON at startup favors
  deterministic local behavior over incremental import infrastructure.
- The generated SQLite database lives in the container's writable layer. It is
  recreated from the supplied files after the container is removed.
- OFFSET pagination and leading-wildcard substring `LIKE` are reasonable for
  56 records, but they are not the intended large-scale design.
- Plain HTML, CSS, and JavaScript avoid a frontend dependency or build
  pipeline.
- Duplicate candidates are surfaced instead of merged automatically because
  source identity cannot be inferred safely.

## 7. How filtering and pagination would change at 500k rows

Exact country and category filters should remain reasonable when their
dimension, junction, and ordering indexes are aligned with the query plan.
Leading-wildcard name and alias `LIKE` searches, however, will stop using
ordinary B-tree indexes efficiently. Deep OFFSET pages become increasingly
expensive and may shift when rows are added or changed. Exact `COUNT` queries
can also become a significant part of request latency.

Before changing storage or search technology, I would benchmark representative
data with query plans and load tests. For a single-node SQLite deployment,
SQLite FTS5 would be the first lexical-search option. If concurrency and
operational requirements justified PostgreSQL, trigram or full-text indexes
would support substring and ranked lexical search.

Pagination would move to keyset/cursor pagination ordered by
`(normalized_name, source_id)`. Responses would return an opaque next cursor
while retaining bounded page sizes. Totals could become optional, cached, or
approximate when exact counts are too expensive. Page hydration should remain
bulk-based, as it is now, rather than introducing N+1 child queries.

On the ingestion side, the current expanded `NOT IN` list is not suitable for
500,000 source IDs. A staging table with bulk, set-based upserts and deletes
should replace it.

## 8. Semantic search approach

Semantic search would start with one searchable document per distributor,
combining its name, aliases, description, categories, countries, and
locations. Embeddings could be generated by a locally hosted open-source model,
so no paid API is required. A multilingual model should be evaluated for
future Africa and MENA data.

Embeddings would be versioned and linked to both `source_id` and
`record_fingerprint`, allowing only changed fingerprints to be re-embedded. A
self-hosted PostgreSQL design could store them in pgvector with an HNSW index;
another self-hosted vector index could serve the same role in a different
deployment.

Retrieval should be hybrid: combine vector similarity with lexical name and
alias matching plus structured country and category filters. Exact names and
aliases should rank above purely semantic matches. Vectors should complement,
not replace, structured filtering.

Quality should be measured with labelled queries using relevance judgments,
Recall@k or nDCG, and latency targets rather than a few informal examples.

## 9. AI-tool disclosure

Codex was used heavily for the staged implementation and automated testing.
ChatGPT was used to examine requirements, challenge schema and API decisions,
and review each stage. Work was divided into bounded stages with a commit after
each accepted stage.

Generated code was inspected and repeatedly tested through Python, Docker, API
requests, and headless-browser checks. Review found and corrected concrete
issues, including oversized-page overflow, control-character handling, and
duplicate alias presentation.

No AI model or paid service is required at runtime. Final scope and acceptance
decisions remained with the developer.

## 10. Testing

For optional local test execution, use Python 3.12:

```sh
python -m pip install -r requirements.txt
python -m pytest
```

The current 54-test suite covers transactional ingestion and rollback,
normalization and duplicate preservation, API filters, errors and pagination,
bulk page loading, frontend assets and structure, and health/documentation
routes.
