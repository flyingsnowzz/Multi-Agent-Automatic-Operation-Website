-- Remove large prompt/reason payload columns from pipeline_audit if they exist.
-- Prompt/reason/breakdown/suggestion payloads are written to logs/prompt_audit/*.jsonl.
USE multi_agent_cms;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'scoring_reason'),
  'ALTER TABLE pipeline_audit DROP COLUMN scoring_reason',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'scoring_breakdown'),
  'ALTER TABLE pipeline_audit DROP COLUMN scoring_breakdown',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'quality_reasons'),
  'ALTER TABLE pipeline_audit DROP COLUMN quality_reasons',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'quality_suggestions'),
  'ALTER TABLE pipeline_audit DROP COLUMN quality_suggestions',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'research_prompt'),
  'ALTER TABLE pipeline_audit DROP COLUMN research_prompt',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'writer_prompt_version'),
  'ALTER TABLE pipeline_audit DROP COLUMN writer_prompt_version',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pipeline_audit' AND COLUMN_NAME = 'image_prompt'),
  'ALTER TABLE pipeline_audit DROP COLUMN image_prompt',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
