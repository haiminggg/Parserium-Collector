-- Records which parser engine a job requested. Additive, so existing rows keep the original engine.
-- The allowlist of engine ids lives in application code, so adding an engine needs no table rebuild.
-- Keep this file free of semicolons in comments because the test harness splits statements on them.
ALTER TABLE parse_jobs ADD COLUMN engine TEXT NOT NULL DEFAULT 'liteparse' CHECK(length(engine)>=1 AND length(engine)<=32);
