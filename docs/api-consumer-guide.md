# API Consumer Guide

A practical guide for researchers, journalists, and developers who want to programmatically access the HealthArchive.

**Audience**: API consumers, data researchers, integration developers
**Time**: 20-30 minutes to read, lifetime to master
**Prerequisites**: Basic HTTP/REST knowledge, command line or programming experience

---

## Quick Start

**Base URL**: `https://api.healtharchive.ca`

**Authentication**: None required for public endpoints (search, stats, snapshots)

**Try it now**:
```bash
curl "https://api.healtharchive.ca/api/stats"
```

---

## API Overview

HealthArchive provides a RESTful JSON API for searching and retrieving archived Canadian health government content.

### Public Endpoints

| Endpoint | Purpose | Auth Required |
|----------|---------|---------------|
| `GET /api/health` | Health check | No |
| `GET /api/stats` | Archive statistics | No |
| `GET /api/sources` | List archived sources | No |
| `GET /api/search` | Search snapshots | No |
| `GET /api/snapshot/{id}` | Get snapshot metadata | No |
| `GET /api/snapshots/raw/{id}` | View archived HTML | No |

### Admin Endpoints

(For operators only, require token)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/admin/jobs` | List crawl jobs |
| `GET /api/admin/jobs/{id}` | Get job details |
| `GET /metrics` | Prometheus metrics |

**This guide focuses on public endpoints.**

---

## Interactive API Documentation

Explore the API interactively:

**Swagger UI**: [https://api.healtharchive.ca/docs](https://api.healtharchive.ca/docs)

Features:
- Try requests directly in browser
- See request/response examples
- View full schema definitions

---

## Core Concepts

### Sources

A **Source** represents a content origin (e.g., Health Canada, PHAC).

**Available sources**:
- `hc` - Health Canada
- `phac` - Public Health Agency of Canada

### Snapshots

A **Snapshot** is a single captured web page at a specific point in time.

**Key attributes**:
- `url`: Original web address
- `captureDate`: When it was archived
- `title`: Page title
- `snippet`: Text preview
- `language`: Content language (`en` or `fr`)

### Views

Search results can be returned in two views:

1. **Snapshots view** (`view=snapshots`, default): Returns individual captures
2. **Pages view** (`view=pages`): Returns only the latest capture per page

---

## API Versioning & Response Headers

### Versioning Strategy

The HealthArchive API uses **header-based versioning** for forward compatibility:

- **Current Version**: `1` (major version only)
- **Header**: `X-API-Version: 1`
- **Stability**: Version 1 is stable; breaking changes will increment to version 2

**Version Header** (returned on all responses):
```
X-API-Version: 1
```

**Versioning Policy**:
- **Major version changes** (1 → 2): Breaking changes to request/response format, removed endpoints
- **Minor updates** (within v1): Additive only (new fields, new optional parameters, new endpoints)
- **Clients should**: Inspect `X-API-Version` header to detect version; log warnings if unexpected

**Deprecation**: If breaking changes are needed, we will:
1. Announce deprecation at least 6 months in advance
2. Run both versions in parallel during transition
3. Provide migration guide in this documentation

### Standard Response Headers

All API responses include these headers:

| Header | Purpose | Example |
|--------|---------|---------|
| `X-API-Version` | API major version | `1` |
| `X-Request-Id` | Request correlation ID | `a3f2e1d0-...` |
| `X-Content-Type-Options` | Security: prevent MIME sniffing | `nosniff` |
| `X-Frame-Options` | Security: clickjacking protection | `SAMEORIGIN` |
| `Referrer-Policy` | Privacy: control referrer info | `strict-origin-when-cross-origin` |

**Security headers** (all responses):

| Header | Purpose | Value |
|--------|---------|-------|
| `Content-Security-Policy` | XSS/injection prevention | See CSP section below |
| `Strict-Transport-Security` | Enforce HTTPS | `max-age=31536000; includeSubDomains` |
| `Permissions-Policy` | Disable sensitive browser features | `geolocation=(), microphone=(), camera=()` |

**Rate-limited endpoints also include**:

| Header | Purpose | Example |
|--------|---------|---------|
| `X-RateLimit-Limit` | Maximum requests allowed in window | `60` |
| `X-RateLimit-Remaining` | Requests remaining in current window | `57` |

**Using Request IDs**:
- Include `X-Request-Id` from response when reporting issues
- Pass custom `X-Request-Id` in request to trace across systems
- IDs are UUIDv4 format and logged server-side for debugging

### Content Security Policy (CSP)

The API implements **Content Security Policy** headers to prevent XSS and code injection attacks.

**For JSON endpoints** (most of the API):
```
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
```
- Blocks all resource loading by default
- Prevents the API from being embedded in iframes

**For HTML replay endpoints** (`/api/snapshots/raw/*`):
```
Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval';
  style-src 'unsafe-inline' *; img-src * data: blob:; font-src * data:;
  connect-src *; media-src *; object-src 'none'; frame-src *;
  base-uri 'self'; form-action 'self'
```
- Allows inline scripts/styles (required for archived HTML)
- Allows external resources (images, fonts, media)
- Still blocks dangerous features (object/embed tags)

**Why this matters for API consumers**:
- CSP headers are informational for JSON API consumers (your code isn't affected)
- If you're embedding `/api/snapshots/raw/*` in iframes, CSP allows it with proper sandboxing
- CSP is automatically relaxed for archived content replay while maintaining security for JSON endpoints

### Request Size Limits

The API enforces size limits to prevent abuse and ensure system stability:

| Limit | Default | Max | Configurable |
|-------|---------|-----|--------------|
| Request body size | 1 MB | 10 MB | Yes |
| Query string length | 8 KB | 64 KB | Yes |

**Error Responses**:
- `413 Payload Too Large`: Request body exceeds size limit
- `414 URI Too Long`: Query string exceeds length limit

**Example 413 response**:
```json
{
  "error": "Payload Too Large",
  "detail": "Request body exceeds maximum size of 1048576 bytes"
}
```

**Example 414 response**:
```json
{
  "error": "URI Too Long",
  "detail": "Query string exceeds maximum length of 8192 characters"
}
```

**Best Practices**:
- Keep issue reports concise (under 1MB)
- Use pagination for large result sets instead of increasing page size
- Filter search queries to reduce result count rather than fetching everything
- The 1MB body limit is sufficient for all standard API operations

---

## Common Use Cases

### 1. Get Archive Statistics

**Use case**: Display total archive size, latest capture, etc.

```bash
curl "https://api.healtharchive.ca/api/stats"
```

**Response**:
```json
{
  "snapshotsTotal": 45678,
  "pagesTotal": 12345,
  "sourcesTotal": 2,
  "latestCaptureDate": "2026-01-18",
  "latestCaptureAgeDays": 0
}
```

**Fields**:
- `snapshotsTotal`: Total captures across all sources
- `pagesTotal`: Unique pages (excluding duplicates)
- `sourcesTotal`: Number of sources
- `latestCaptureDate`: Most recent capture timestamp
- `latestCaptureAgeDays`: Days since latest capture

---

### 2. List All Sources

**Use case**: Understand what's in the archive

```bash
curl "https://api.healtharchive.ca/api/sources"
```

**Response**:
```json
[
  {
    "sourceCode": "hc",
    "sourceName": "Health Canada",
    "recordCount": 30123,
    "firstCapture": "2024-06-01T00:00:00Z",
    "lastCapture": "2026-01-18T21:15:42Z",
    "latestRecordId": 12345
  },
  {
    "sourceCode": "phac",
    "sourceName": "Public Health Agency of Canada",
    "recordCount": 15555,
    "firstCapture": "2024-06-01T00:00:00Z",
    "lastCapture": "2026-01-18T20:30:15Z",
    "latestRecordId": 12346
  }
]
```

---

### 3. Search for Content

**Use case**: Find pages about a specific topic

#### Basic Keyword Search

```bash
curl "https://api.healtharchive.ca/api/search?q=covid vaccines"
```

**Response**:
```json
{
  "results": [
    {
      "id": 1,
      "title": "COVID-19 vaccines: Authorization and safety",
      "sourceCode": "hc",
      "sourceName": "Health Canada",
      "language": "en",
      "captureDate": "2026-01-18T21:15:42Z",
      "originalUrl": "https://www.canada.ca/en/health-canada/services/drugs-health-products/covid19-industry/drugs-vaccines-treatments/vaccines.html",
      "snippet": "Health Canada has approved several COVID-19 vaccines for use in Canada...",
      "rawSnapshotUrl": "/api/snapshots/raw/1"
    }
  ],
  "total": 127,
  "page": 1,
  "pageSize": 20
}
```

#### Filter by Source

```bash
curl "https://api.healtharchive.ca/api/search?q=vaccines&source=phac"
```

Only returns results from PHAC.

#### Sort by Date (Newest First)

```bash
curl "https://api.healtharchive.ca/api/search?q=vaccines&sort=newest"
```

**Sort options**:
- `relevance` (default when `q` is present): Best match first
- `newest`: Most recent captures first

#### Filter by Date Range

```bash
# Captures from 2025 only
curl "https://api.healtharchive.ca/api/search?q=vaccines&from=2025-01-01&to=2025-12-31"
```

#### Include Non-2xx HTTP Status

By default, only successful (200-299) responses are returned. To include redirects, errors, etc.:

```bash
curl "https://api.healtharchive.ca/api/search?q=vaccines&includeNon2xx=true"
```

#### Pagination

```bash
# Get page 2, 50 results per page
curl "https://api.healtharchive.ca/api/search?q=vaccines&page=2&pageSize=50"
```

**Limits**:
- `page`: Min 1 (default: 1)
- `pageSize`: Min 1, Max 100 (default: 20)

---

### 4. Advanced Search Syntax

#### Boolean Operators

```bash
# AND (both terms must appear)
curl "https://api.healtharchive.ca/api/search?q=covid+AND+vaccine"

# OR (either term)
curl "https://api.healtharchive.ca/api/search?q=covid+OR+coronavirus"

# NOT (exclude term)
curl "https://api.healtharchive.ca/api/search?q=vaccine+NOT+flu"

# Parentheses for grouping
curl "https://api.healtharchive.ca/api/search?q=(covid+OR+coronavirus)+AND+vaccine"
```

#### Field-Specific Search

```bash
# Search only in title
curl "https://api.healtharchive.ca/api/search?q=title:vaccines"

# Search only in snippet (text content)
curl "https://api.healtharchive.ca/api/search?q=snippet:mRNA"

# Search only in URL
curl "https://api.healtharchive.ca/api/search?q=url:health-canada"
```

#### URL Lookup

Find all captures of a specific page:

```bash
curl "https://api.healtharchive.ca/api/search?q=url:https://www.canada.ca/en/health-canada.html"
```

Or use the `url:` prefix:

```bash
curl "https://api.healtharchive.ca/api/search?q=url:canada.ca/en/health-canada"
```

---

### 5. Browse Pages (Latest Captures Only)

**Use case**: Get a list of unique pages, not all captures

```bash
curl "https://api.healtharchive.ca/api/search?view=pages&source=hc&sort=newest"
```

**Difference from snapshots view**:
- `view=snapshots`: Returns all captures (same page may appear multiple times)
- `view=pages`: Returns only the most recent capture per page

**Response** includes `pageSnapshotsCount`:
```json
{
  "results": [
    {
      "id": 12345,
      "title": "Health Canada",
      "pageSnapshotsCount": 15,
      ...
    }
  ]
}
```

`pageSnapshotsCount` tells you how many times this page was captured.

---

### 6. Get Snapshot Metadata

**Use case**: Retrieve full details for a specific snapshot

```bash
curl "https://api.healtharchive.ca/api/snapshot/12345"
```

**Response**:
```json
{
  "id": 12345,
  "title": "COVID-19 vaccines",
  "sourceCode": "hc",
  "sourceName": "Health Canada",
  "language": "en",
  "captureDate": "2026-01-18T21:15:42Z",
  "originalUrl": "https://www.canada.ca/en/health-canada/services/drugs-health-products/covid19-industry/drugs-vaccines-treatments/vaccines.html",
  "mimeType": "text/html",
  "statusCode": 200,
  "snippet": "Health Canada has approved...",
  "rawSnapshotUrl": "/api/snapshots/raw/12345"
}
```

---

### 7. View Archived HTML

**Use case**: Retrieve the actual archived page content

```bash
curl "https://api.healtharchive.ca/api/snapshots/raw/12345"
```

**Response**: HTML page with HealthArchive header banner

**In browser**: Visit `https://api.healtharchive.ca/api/snapshots/raw/12345` to see rendered page

**Note**: This is the archived content exactly as it was captured, plus a small HealthArchive navigation bar.

---

## Language Support

HealthArchive indexes content in English and French.

### Search by Language

```bash
# English content only
curl "https://api.healtharchive.ca/api/search?q=vaccines&language=en"

# French content only
curl "https://api.healtharchive.ca/api/search?q=vaccins&language=fr"
```

**Tip**: HealthArchive auto-detects language, but some pages may be incorrectly classified.

---

## Pagination & Performance

### Response Times

- **Search**: ~100-500ms (depending on query complexity)
- **Stats**: ~50ms (heavily cached)
- **Sources**: ~100ms
- **Snapshot metadata**: ~50ms
- **Raw HTML**: ~200-500ms (reads WARC from disk)

### Rate Limiting

**Rate limits are enforced per client IP address** to ensure fair resource allocation and prevent abuse.

| Endpoint | Limit | Window |
|----------|-------|--------|
| `POST /api/reports` | 5 requests | per minute |
| `GET /api/exports/*` | 10 requests | per minute |
| `GET /api/search` | 60 requests | per minute |
| All other endpoints | 120 requests | per minute |

**Rate limit headers** (included in responses to limited endpoints):
```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 57
```

**When limits are exceeded**:
- HTTP status: `429 Too Many Requests`
- Response includes `Retry-After` header (seconds until limit resets)
- Example error response:
```json
{
  "error": "Rate limit exceeded",
  "detail": "60 per 1 minute"
}
```

**Best practices**:
- Monitor `X-RateLimit-Remaining` header to avoid hitting limits
- Implement exponential backoff when you receive 429 responses
- Cache responses when appropriate to reduce request volume
- Use `pageSize` wisely (larger pages = slower but fewer requests)
- For bulk exports, use the `/api/exports/*` endpoints instead of paginating search
- Contact the maintainers if you need higher limits for legitimate research

### Pagination Best Practices

**For complete datasets**:
```python
import requests

base_url = "https://api.healtharchive.ca/api/search"
page = 1
page_size = 100  # Max allowed
all_results = []

while True:
    response = requests.get(base_url, params={
        "q": "vaccines",
        "page": page,
        "pageSize": page_size
    })
    data = response.json()

    all_results.extend(data["results"])

    if page * page_size >= data["total"]:
        break

    page += 1

print(f"Retrieved {len(all_results)} results")
```

---

## Code Examples

### Python

```python
import requests

def search_healtharchive(query, source=None, sort="relevance", page=1):
    """Search HealthArchive API."""
    url = "https://api.healtharchive.ca/api/search"
    params = {
        "q": query,
        "sort": sort,
        "page": page,
        "pageSize": 20
    }
    if source:
        params["source"] = source

    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()

# Example usage
results = search_healtharchive("covid vaccines", source="hc")
for snapshot in results["results"]:
    print(f"{snapshot['title']} - {snapshot['captureDate']}")
```

### JavaScript (Node.js)

```javascript
const fetch = require('node-fetch');

async function searchHealthArchive(query, options = {}) {
    const baseUrl = 'https://api.healtharchive.ca/api/search';
    const params = new URLSearchParams({
        q: query,
        sort: options.sort || 'relevance',
        page: options.page || 1,
        pageSize: options.pageSize || 20,
        ...(options.source && { source: options.source })
    });

    const response = await fetch(`${baseUrl}?${params}`);
    return response.json();
}

// Example usage
(async () => {
    const results = await searchHealthArchive('covid vaccines', { source: 'hc' });
    results.results.forEach(snapshot => {
        console.log(`${snapshot.title} - ${snapshot.captureDate}`);
    });
})();
```

### R

```r
library(httr)
library(jsonlite)

search_healtharchive <- function(query, source = NULL, sort = "relevance", page = 1) {
  base_url <- "https://api.healtharchive.ca/api/search"

  params <- list(
    q = query,
    sort = sort,
    page = page,
    pageSize = 20
  )

  if (!is.null(source)) {
    params$source <- source
  }

  response <- GET(base_url, query = params)
  stop_for_status(response)

  content(response, as = "parsed")
}

# Example usage
results <- search_healtharchive("covid vaccines", source = "hc")
for (snapshot in results$results) {
  cat(sprintf("%s - %s\n", snapshot$title, snapshot$captureDate))
}
```

### Shell (curl + jq)

```bash
#!/bin/bash

# Search and format results
curl -s "https://api.healtharchive.ca/api/search?q=vaccines&source=hc" | \
  jq -r '.results[] | "\(.title) - \(.captureDate)"'

# Get total count
curl -s "https://api.healtharchive.ca/api/search?q=vaccines" | \
  jq '.total'

# Download all snapshot URLs
curl -s "https://api.healtharchive.ca/api/search?q=vaccines&pageSize=100" | \
  jq -r '.results[] | .originalUrl' > urls.txt
```

---

## Research Workflows

### 1. Historical Analysis

**Goal**: Track how Health Canada's COVID-19 vaccine page changed over time

```python
import requests
from datetime import datetime

url_to_track = "https://www.canada.ca/en/health-canada/services/drugs-health-products/covid19-industry/drugs-vaccines-treatments/vaccines.html"

response = requests.get(
    "https://api.healtharchive.ca/api/search",
    params={
        "q": f"url:{url_to_track}",
        "view": "snapshots",  # Get all captures
        "sort": "newest",
        "pageSize": 100
    }
)

snapshots = response.json()["results"]

print(f"Found {len(snapshots)} captures of this page")

for snapshot in snapshots:
    capture_date = datetime.fromisoformat(snapshot["captureDate"].replace("Z", "+00:00"))
    print(f"{capture_date.strftime('%Y-%m-%d')}: {snapshot['title']}")
```

### 2. Comparative Analysis

**Goal**: Compare coverage of a topic across sources

```python
import requests

topic = "vaccination"

for source in ["hc", "phac"]:
    response = requests.get(
        "https://api.healtharchive.ca/api/search",
        params={"q": topic, "source": source, "pageSize": 1}
    )
    total = response.json()["total"]
    print(f"{source.upper()}: {total} snapshots mention '{topic}'")
```

### 3. Bulk Download Metadata

**Goal**: Export all metadata for offline analysis

```python
import requests
import json

all_snapshots = []
page = 1

while True:
    response = requests.get(
        "https://api.healtharchive.ca/api/search",
        params={
            "q": "",  # Empty query = browse all
            "page": page,
            "pageSize": 100,
            "sort": "newest"
        }
    )
    data = response.json()
    all_snapshots.extend(data["results"])

    if page * 100 >= data["total"]:
        break

    page += 1

# Save to JSON
with open("healtharchive_metadata.json", "w") as f:
    json.dump(all_snapshots, f, indent=2)

print(f"Exported {len(all_snapshots)} snapshots")
```

---

## Citation & Attribution

### Citing HealthArchive

When using HealthArchive data in research:

```
HealthArchive. (2026). Archive of Canadian Health Government Websites.
Retrieved [Date] from https://healtharchive.ca
```

### Citing Specific Snapshots

```
Health Canada. (2026, January 18). COVID-19 vaccines: Authorization and safety.
Archived by HealthArchive. Retrieved from https://api.healtharchive.ca/api/snapshots/raw/12345
Original URL: https://www.canada.ca/en/health-canada/services/drugs-health-products/covid19-industry/drugs-vaccines-treatments/vaccines.html
```

---

## Data Access & Datasets

### Bulk Data Downloads

For large-scale research, consider using dataset releases:

**Datasets Repository**: [github.com/jerdaw/healtharchive-datasets](https://github.com/jerdaw/healtharchive-datasets)

**Benefits**:
- Pre-packaged metadata exports
- Checksums for integrity verification
- Version-controlled releases
- Citable DOIs (future)

### API vs Datasets

| Use Case | Use API | Use Dataset |
|----------|---------|-------------|
| Real-time search | ✅ | ❌ |
| Small queries (< 1000 results) | ✅ | ❌ |
| Complete metadata export | ❌ | ✅ |
| Reproducible research | ~ | ✅ |
| Offline analysis | ❌ | ✅ |

---

## Error Handling

### Common HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process response |
| 404 | Snapshot/resource not found | Check ID, may have been deleted |
| 422 | Validation error | Fix query parameters |
| 500 | Server error | Retry with exponential backoff |
| 503 | Service unavailable | Maintenance, retry later |

### Example Error Response

```json
{
  "detail": [
    {
      "loc": ["query", "page"],
      "msg": "ensure this value is greater than or equal to 1",
      "type": "value_error.number.not_ge"
    }
  ]
}
```

### Robust Error Handling (Python)

```python
import requests
import time

def search_with_retry(query, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(
                "https://api.healtharchive.ca/api/search",
                params={"q": query},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code >= 500:
                # Server error, retry with backoff
                wait = 2 ** attempt
                print(f"Server error, retrying in {wait}s...")
                time.sleep(wait)
            else:
                # Client error, don't retry
                raise
        except requests.exceptions.Timeout:
            print(f"Timeout, retrying...")
            time.sleep(2 ** attempt)

    raise Exception(f"Failed after {max_retries} retries")
```

---

## API Limits & Fair Use

### Current Limits

- **Rate limiting**: None (subject to change)
- **Query complexity**: No hard limits, but very broad queries may timeout
- **Page size**: Max 100 results per page

### Fair Use Guidelines

To keep the API available for everyone:

1. **Cache aggressively**: Don't request the same data repeatedly
2. **Use appropriate page sizes**: Don't always use `pageSize=100` if you only need 20
3. **Implement backoff**: Retry with exponential backoff on errors
4. **Consider datasets**: For bulk access, use dataset releases instead of paginating through API
5. **Report issues**: If you encounter consistent errors, let us know

### Future Changes

We may introduce:
- Rate limiting (per IP or API key)
- API keys for higher limits
- Tiered access (free vs. paid)

**Stay informed**: Monitor [github.com/jerdaw/healtharchive-backend](https://github.com/jerdaw/healtharchive-backend) for announcements

---

## FAQ

**Q: Is there an API key or authentication?**
A: Public endpoints require no authentication. Admin endpoints require a token.

**Q: Can I download the entire archive?**
A: Use dataset releases for bulk access. API is designed for queries, not full dumps.

**Q: How often is the archive updated?**
A: Annual full crawls, with potential ad-hoc crawls for significant events.

**Q: What if a snapshot I need is missing?**
A: Check the capture dates via `/api/sources`. We can only provide what was archived.

**Q: Can I request a specific page be archived?**
A: Currently no on-demand archiving. Future feature under consideration.

**Q: How long are snapshots retained?**
A: Indefinitely, subject to storage constraints. See [Data Handling Policy](operations/data-handling-retention.md).

**Q: Is there a GraphQL API?**
A: Not yet. REST/JSON only for now.

**Q: Can I embed archived pages in my site?**
A: Yes, use `<iframe src="https://api.healtharchive.ca/api/snapshots/raw/{id}"></iframe>`. Attribute HealthArchive.

---

## Support & Contact

- **Technical issues**: [GitHub Issues](https://github.com/jerdaw/healtharchive-backend/issues)
- **General questions**: [GitHub Discussions](https://github.com/jerdaw/healtharchive-backend/discussions)
- **API documentation**: [Interactive docs](https://api.healtharchive.ca/docs)

---

## Next Steps

- **Explore the API**: Try [interactive documentation](https://api.healtharchive.ca/docs)
- **Download datasets**: Visit [healtharchive-datasets](https://github.com/jerdaw/healtharchive-datasets)
- **Read the architecture**: [Architecture Guide](architecture.md)
- **Stay updated**: Watch the [backend repo](https://github.com/jerdaw/healtharchive-backend) for changes

Happy researching! 📊
