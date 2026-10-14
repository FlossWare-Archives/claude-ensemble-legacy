# Operational Metrics REST API

The canonical gateway exposes the append-only operational metrics ledger at
`/api/v1/metrics`. Listing is paginated so the HTTP response and retained page
remain bounded as the ledger grows.

## List metrics

`GET /api/v1/metrics?limit=100&offset=0`

- `limit` defaults to `100` and must be between `1` and `500`.
- `offset` defaults to `0` and must be a non-negative integer.
- Unknown, duplicate, blank, or malformed query parameters return HTTP `400`.
- Records are returned in chronological order, skipping malformed JSONL records.
- `next_offset` is the next offset when another page exists, otherwise `null`.

Example response:

```json
{
  "ok": true,
  "metrics": [],
  "limit": 100,
  "offset": 0,
  "next_offset": null
}
```

Clients should request the returned `next_offset` until it is `null`. Offset
pagination reflects the ledger as it exists at each request; records appended
between page requests may appear in later pages. Consumers requiring a stable
snapshot should copy/export the ledger or use a separate snapshot mechanism.

## Aggregate metrics

`GET /api/v1/metrics/aggregate` streams the ledger and returns counts by
service, model, and status, total estimated cost, and average latency. The
aggregation does not construct a list of all metric records.

## Write metrics

`POST /api/v1/metrics` accepts one metric record and returns HTTP `201` when
the record is appended. Metrics are operational telemetry, not evidence of
task correctness or learned outcomes.

## CSV export safety

MetricsStore.export_csv() is spreadsheet-safe by default. Formula-like string
cells whose first non-whitespace character is =, +, -, or @ (or a leading tab
or carriage return) are prefixed with an apostrophe at export time. Numeric
fields retain their numeric representation, CSV quoting remains handled by the
standard CSV writer, and the canonical JSONL ledger is unchanged.

For trusted machine consumers that require exact text, call
export_csv(destination, spreadsheet_safe=False). Raw mode preserves formula-like
strings and must not be opened in spreadsheet software with untrusted records.
