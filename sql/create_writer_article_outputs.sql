-- WriterAgent generated article outputs.
-- Stored in the same database as research_article_candidates.

USE `research_article_data`;

CREATE TABLE IF NOT EXISTS `writer_article_outputs` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Primary key',
  `candidate_id` BIGINT UNSIGNED NOT NULL COMMENT 'research_article_candidates.id',
  `source_article_id` BIGINT UNSIGNED NULL COMMENT 'Original crawler article id',
  `original_url` VARCHAR(1024) NOT NULL COMMENT 'Original article URL',
  `source_title` VARCHAR(512) NULL COMMENT 'Original article title',
  `article_score` DECIMAL(5,2) NOT NULL COMMENT 'Source article score',
  `writer_prompt` MEDIUMTEXT NOT NULL COMMENT 'Prompt used for generation',
  `writer_model` VARCHAR(100) NULL COMMENT 'LLM model name',
  `generation_status` VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending/generated/failed',
  `generated_title` VARCHAR(512) NULL COMMENT 'Generated article title',
  `generated_meta_description` VARCHAR(512) NULL COMMENT 'Generated meta description',
  `generated_content_md` LONGTEXT NULL COMMENT 'Generated Markdown article',
  `generated_article_json` JSON NULL COMMENT 'Full WriterAgent JSON output',
  `quality_checks` JSON NULL COMMENT 'WriterAgent quality checks if available',
  `warnings` JSON NULL COMMENT 'WriterAgent warnings if available',
  `error_message` TEXT NULL COMMENT 'Generation error if failed',
  `generated_at` DATETIME NULL COMMENT 'When generation succeeded',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_candidate_id` (`candidate_id`),
  KEY `idx_generation_status` (`generation_status`),
  KEY `idx_article_score` (`article_score`),
  KEY `idx_source_article` (`source_article_id`),
  CONSTRAINT `fk_writer_article_candidate`
    FOREIGN KEY (`candidate_id`)
    REFERENCES `research_article_candidates` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='WriterAgent generated articles from research candidates';

-- Example:
-- SELECT candidate_id, source_title, article_score, generated_title, generation_status
-- FROM writer_article_outputs
-- ORDER BY article_score DESC, candidate_id ASC;
