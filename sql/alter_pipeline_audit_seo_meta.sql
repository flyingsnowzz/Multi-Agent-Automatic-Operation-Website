SET @db := DATABASE();

SET @has_seo_meta_title := (
  SELECT COUNT(*)
  FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = @db
    AND TABLE_NAME = 'pipeline_audit'
    AND COLUMN_NAME = 'seo_meta_title'
);

SET @sql := IF(
  @has_seo_meta_title = 0,
  'ALTER TABLE `pipeline_audit` ADD COLUMN `seo_meta_title` VARCHAR(512) NULL COMMENT ''SEO meta title'' AFTER `image_local_path`',
  'SELECT ''seo_meta_title exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_seo_meta_description := (
  SELECT COUNT(*)
  FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = @db
    AND TABLE_NAME = 'pipeline_audit'
    AND COLUMN_NAME = 'seo_meta_description'
);

SET @sql := IF(
  @has_seo_meta_description = 0,
  'ALTER TABLE `pipeline_audit` ADD COLUMN `seo_meta_description` TEXT NULL COMMENT ''SEO meta description'' AFTER `seo_meta_title`',
  'SELECT ''seo_meta_description exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE `pipeline_audit`
SET
  `seo_meta_title` = COALESCE(
    NULLIF(`seo_meta_title`, ''),
    NULLIF(JSON_UNQUOTE(JSON_EXTRACT(`seo_keywords`, '$.meta_title')), '')
  ),
  `seo_meta_description` = COALESCE(
    NULLIF(`seo_meta_description`, ''),
    NULLIF(JSON_UNQUOTE(JSON_EXTRACT(`seo_keywords`, '$.meta_description')), '')
  )
WHERE `seo_keywords` IS NOT NULL;
