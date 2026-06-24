-- Research article candidate database.
-- Keeps a clean writer-facing pool separate from crawler/source tables.
-- Source crawler data remains unchanged; this table stores selected 75-90 score articles
-- plus the ResearchAgent prompt package for WriterAgent.

CREATE DATABASE IF NOT EXISTS `research_article_data`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `research_article_data`;

CREATE TABLE IF NOT EXISTS `research_article_candidates` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `source_database` VARCHAR(128) NOT NULL DEFAULT 'crawler_ai' COMMENT 'Original database name',
  `source_table` VARCHAR(128) NOT NULL DEFAULT 'crawler_news_main' COMMENT 'Original table name',
  `source_article_id` BIGINT UNSIGNED NULL COMMENT 'Original crawler article id',
  `original_url` VARCHAR(1024) NOT NULL COMMENT 'Original article URL',
  `title` VARCHAR(512) NULL COMMENT 'Original article title',
  `college_name` VARCHAR(255) NULL COMMENT 'College name copied for writer context',
  `specialty_name` VARCHAR(255) NULL COMMENT 'Specialty name copied for writer context',
  `category` VARCHAR(64) NULL COMMENT 'Original category copied for context',
  `publish_date` VARCHAR(64) NULL COMMENT 'Original publish date',
  `word_count` INT UNSIGNED NULL COMMENT 'Article word count from scorer',
  `article_score` DECIMAL(5,2) NOT NULL COMMENT 'Overall article score, normally 75-90',
  `title_style_score` DECIMAL(5,2) NULL COMMENT 'Title score from scorer',
  `content_importance_score` DECIMAL(5,2) NULL COMMENT 'Final importance score after freshness factor',
  `raw_content_importance_score` DECIMAL(5,2) NULL COMMENT 'Raw AI importance score',
  `length_score` DECIMAL(5,2) NULL COMMENT 'Length score from scorer',
  `freshness_score` DECIMAL(5,2) NULL COMMENT 'Freshness score from scorer',
  `is_notice` TINYINT(1) NULL COMMENT 'Whether article is notice/admin content',
  `keep_reason` VARCHAR(512) NULL COMMENT 'Reason this row entered the research pool',
  `score_payload` JSON NULL COMMENT 'Full scoring payload for audit',
  `research_status` VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending/generated/failed/consumed',
  `research_brief` JSON NULL COMMENT 'ResearchAgent structured brief',
  `writer_prompt` MEDIUMTEXT NULL COMMENT 'Prompt package for WriterAgent',
  `writer_prompt_type` VARCHAR(100) NULL COMMENT 'Prompt type/version emitted by ResearchAgent',
  `prompt_version` VARCHAR(50) NOT NULL DEFAULT 'research_prompt_v1' COMMENT 'Internal prompt version',
  `prompt_generated_at` DATETIME NULL COMMENT 'When writer_prompt was generated',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_original_url` (`original_url`(255)),
  KEY `idx_article_score` (`article_score`),
  KEY `idx_research_status` (`research_status`),
  KEY `idx_source_article` (`source_table`, `source_article_id`),
  KEY `idx_prompt_generated_at` (`prompt_generated_at`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Clean research candidate pool for WriterAgent';

-- WriterAgent fetch example:
-- SELECT id, original_url, title, article_score, writer_prompt
-- FROM research_article_candidates
-- WHERE research_status = 'generated'
-- ORDER BY article_score DESC, id ASC;

-- Optional initial load example, after scoring columns exist in crawler_ai.crawler_news_main.
-- Keep this commented if your source database name is not crawler_ai.
--
-- INSERT INTO research_article_data.research_article_candidates (
--   source_database,
--   source_table,
--   source_article_id,
--   original_url,
--   title,
--   college_name,
--   specialty_name,
--   category,
--   publish_date,
--   word_count,
--   article_score,
--   title_style_score,
--   content_importance_score,
--   raw_content_importance_score,
--   length_score,
--   freshness_score,
--   is_notice,
--   keep_reason,
--   score_payload,
--   research_status
-- )
-- SELECT
--   'crawler_ai',
--   'crawler_news_main',
--   id,
--   original_url,
--   title,
--   college_name,
--   specialty_name,
--   CAST(category AS CHAR),
--   publish_date,
--   article_word_count,
--   article_overall_score,
--   article_title_style_score,
--   article_content_importance_score,
--   article_raw_content_importance_score,
--   article_length_score,
--   article_freshness_score,
--   article_is_notice,
--   'score_in_75_90_and_writer_ready',
--   JSON_OBJECT(
--     'overall_score', article_overall_score,
--     'title_style_score', article_title_style_score,
--     'content_importance_score', article_content_importance_score,
--     'raw_content_importance_score', article_raw_content_importance_score,
--     'length_score', article_length_score,
--     'freshness_score', article_freshness_score,
--     'is_notice', article_is_notice,
--     'reasons', article_score_reasons
--   ),
--   'pending'
-- FROM crawler_ai.crawler_news_main
-- WHERE article_overall_score BETWEEN 75 AND 90
--   AND original_url IS NOT NULL
--   AND original_url <> ''
--   AND (
--     (article_is_notice IS NULL OR article_is_notice = 0)
--     OR STR_TO_DATE(LEFT(publish_date, 10), '%Y-%m-%d') >= DATE_SUB(CURDATE(), INTERVAL 2 MONTH)
--   )
--   AND (
--     title NOT REGEXP '通知|公告|公示|名单|须知|值班|放假|缴费|补录|调剂复试名单|资格审查'
--     OR STR_TO_DATE(LEFT(publish_date, 10), '%Y-%m-%d') >= DATE_SUB(CURDATE(), INTERVAL 2 MONTH)
--   )
-- ON DUPLICATE KEY UPDATE
--   title = VALUES(title),
--   article_score = VALUES(article_score),
--   score_payload = VALUES(score_payload),
--   research_status = IF(writer_prompt IS NULL, 'pending', research_status);
