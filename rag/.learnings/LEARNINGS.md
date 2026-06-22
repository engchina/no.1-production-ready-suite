# Learnings

## [LRN-20260622-001] correction

**Logged**: 2026-06-22T12:45:00+09:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Cross-cutting HTTP reliability requests must be implemented as shared infrastructure, not as a single caller-specific retry.

### Details
The initial fix focused on parser service retry after a parser-unstructured connection issue. The user clarified that all HTTP service calls in the system need a common exponential retry policy with 5 attempts.

### Suggested Action
When a user describes retry, timeout, or availability behavior for “the system” or “all services”, first identify every production HTTP service call site and route them through a shared helper with tests.

### Metadata
- Source: user_feedback
- Related Files: backend/app/clients/http_retry.py
- Tags: retry, http, reliability, shared-infrastructure

---
