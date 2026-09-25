-- Oracle Deep Data Security supports MATERIALIZED VIEW targets.
-- Keep existing rows and widen the target type discriminator accordingly.

ALTER TABLE NL2SQL_APP_DATA_ENTITLEMENTS MODIFY (TARGET_TYPE VARCHAR2(32));
