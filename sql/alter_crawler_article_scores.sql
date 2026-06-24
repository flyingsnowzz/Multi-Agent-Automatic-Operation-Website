-- Crawler article scoring migration.
-- Purpose:
--   Add dedicated article scoring columns to crawler_news_main without changing
--   the original crawler_data.sql dump or overwriting the existing score fields.
--
-- Apply once before writing article scoring results back to MySQL.
-- Target table: crawler_news_main

ALTER TABLE `crawler_news_main`
  ADD COLUMN `article_overall_score` DECIMAL(5,2) NULL COMMENT '文章评分Agent综合分',
  ADD COLUMN `article_title_style_score` DECIMAL(5,2) NULL COMMENT 'AI标题风格分',
  ADD COLUMN `article_content_importance_score` DECIMAL(5,2) NULL COMMENT 'AI内容重要性分',
  ADD COLUMN `article_freshness_score` DECIMAL(5,2) NULL COMMENT '发布时间时效性规则分',
  ADD COLUMN `article_score_breakdown` JSON NULL COMMENT '文章评分加权明细JSON',
  ADD COLUMN `article_word_count` INT UNSIGNED NULL COMMENT '文章字数统计',
  ADD COLUMN `article_topic_count` INT UNSIGNED NULL COMMENT '解释性topic命中数量',
  ADD COLUMN `article_topics` JSON NULL COMMENT '解释性topic列表JSON，不参与综合分',
  ADD COLUMN `article_score_reasons` JSON NULL COMMENT '文章评分原因列表JSON',
  ADD COLUMN `article_ai_used` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '文章评分是否成功使用AI',
  ADD COLUMN `article_ai_reason` TEXT NULL COMMENT 'AI评分原因',
  ADD COLUMN `article_scoring_model` VARCHAR(100) NULL COMMENT '文章评分使用模型',
  ADD COLUMN `article_scoring_version` VARCHAR(50) NULL COMMENT '文章评分算法版本',
  ADD COLUMN `article_scored_at` DATETIME NULL COMMENT '文章评分写入时间',
  ADD INDEX `idx_article_overall_score` (`article_overall_score`),
  ADD INDEX `idx_article_scored_at` (`article_scored_at`),
  ADD INDEX `idx_article_ai_used` (`article_ai_used`);

-- Optional read query after scoring:
--
-- SELECT
--   id,
--   title,
--   article_overall_score,
--   article_title_style_score,
--   article_content_importance_score,
--   article_freshness_score,
--   article_ai_used,
--   article_scored_at
-- FROM crawler_news_main
-- WHERE article_scored_at IS NOT NULL
-- ORDER BY article_overall_score DESC, id ASC;
