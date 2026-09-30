## 1. Report list API

- [x] 1.1 Add validated page and page-size parameters plus total-count metadata to the report-list endpoint.
- [x] 1.2 Ensure out-of-range page requests resolve to the last valid page and preserve descending creation-time order.

## 2. Report list interface

- [x] 2.1 Add report pagination state and load the current page from the new API response.
- [x] 2.2 Add total count, current-page summary, page-number navigation, and disabled boundary controls.
- [x] 2.3 Refresh the effective page after deleting reports and use total count in the dashboard.

## 3. Verification

- [x] 3.1 Add and run focused API pagination tests, including out-of-range behavior.
- [x] 3.2 Verify the report page interaction in a local browser with multiple pages of report data.
