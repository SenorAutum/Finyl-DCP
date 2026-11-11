-- WP11: the location rule applies for the whole working session.
--
-- Before WP11 the location was required at sign-in, but a request without a
-- location was let through afterwards, so switching location off after signing
-- in avoided the rule. Now every request must carry a recent location while
-- the rule is on. This column is how recent, in minutes (blank = 15).
SET search_path TO finyl_dcp, public;

ALTER TABLE tenant_security_config ADD COLUMN IF NOT EXISTS location_max_age_minutes INTEGER;
