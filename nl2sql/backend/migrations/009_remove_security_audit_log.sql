-- Remove the deprecated application security audit log feature.
-- Existing auth/RBAC/session/DeepSec objects remain intact.

DELETE FROM NL2SQL_APP_ROLE_PERMISSIONS
 WHERE PERMISSION_CODE IN ('security.audit.view', 'menu.security_audit');

DELETE FROM RAG_APP_ROLE_PERMISSIONS
 WHERE PERMISSION_CODE IN ('security.audit.view', 'menu.security_audit');

DROP INDEX IX_NL2SQL_AUTH_AUDIT_TIME;
DROP TABLE NL2SQL_AUTH_AUDIT_LOG PURGE;

DROP INDEX IX_RAG_AUTH_AUDIT_TIME;
DROP TABLE RAG_AUTH_AUDIT_LOG PURGE;
