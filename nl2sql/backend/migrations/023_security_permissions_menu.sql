-- Split role management and permission management (#206).
-- Roles that could manage role permissions keep that ability through the new permission code.

INSERT INTO NL2SQL_APP_ROLE_PERMISSIONS (ROLE_ID, PERMISSION_CODE)
SELECT existing_role.ROLE_ID, 'menu.security_permissions'
  FROM NL2SQL_APP_ROLE_PERMISSIONS existing_role
 WHERE existing_role.PERMISSION_CODE = 'menu.security_roles'
   AND NOT EXISTS (
       SELECT 1
         FROM NL2SQL_APP_ROLE_PERMISSIONS existing
        WHERE existing.ROLE_ID = existing_role.ROLE_ID
          AND existing.PERMISSION_CODE = 'menu.security_permissions'
   );
