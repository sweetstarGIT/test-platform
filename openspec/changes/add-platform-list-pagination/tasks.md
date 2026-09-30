## 1. Paginated list APIs

- [x] 1.1 Add optional, validated pagination parameters and stable ordering to the package-list API while preserving its no-parameter array response.
- [x] 1.2 Add optional pagination and full status-count metadata to the task-list API while preserving its no-parameter array response.
- [x] 1.3 Add optional pagination and full status-count metadata to the CI-job-list API while preserving its no-parameter array response.

## 2. List user interface

- [x] 2.1 Add independent package, task and CI pagination state, totals and shared page-navigation helpers to the SPA.
- [x] 2.2 Update package and task views to show total/range/page navigation and preserve valid pages after mutating actions.
- [x] 2.3 Update CI list and its status cards to use paginated data plus server-provided all-record status counts.
- [x] 2.4 Remove report-list polling from the global timer while retaining initial, navigation and deletion refreshes.

## 3. Documentation and verification

- [x] 3.1 Document the optional pagination contracts and legacy no-parameter compatibility in the router reference.
- [x] 3.2 Add focused API tests for all three paginated lists, aggregate counts, ordering, out-of-range behavior and legacy responses.
- [x] 3.3 Run focused tests and perform a local browser check of navigation plus non-refreshing report list behavior.
