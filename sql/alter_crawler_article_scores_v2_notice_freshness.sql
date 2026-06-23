-- Article scoring v2 migration.
-- Adds notice classification and freshness penalty fields.
-- Run after sql/alter_crawler_article_scores.sql.

ALTER TABLE `crawler_news_main`
  ADD COLUMN `article_is_notice` TINYINT(1) NULL COMMENT 'AI判断是否为通知/公告类内容',
  ADD COLUMN `article_notice_score` DECIMAL(5,2) NULL COMMENT '非通知/新闻类加分项，通知为0分',
  ADD COLUMN `article_raw_content_importance_score` DECIMAL(5,2) NULL COMMENT 'AI原始内容重要性分，未乘时效系数',
  ADD COLUMN `article_freshness_factor` DECIMAL(4,2) NULL COMMENT '时效惩罚系数，用于折算内容重要性',
  ADD COLUMN `article_freshness_weight_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '时效分是否参与综合分；两个月内为0',
  ADD INDEX `idx_article_is_notice` (`article_is_notice`),
  ADD INDEX `idx_article_freshness_factor` (`article_freshness_factor`);

-- Example query:
-- SELECT
--   id,
--   title,
--   article_overall_score,
--   article_is_notice,
--   article_notice_score,
--   article_raw_content_importance_score,
--   article_content_importance_score,
--   article_freshness_score,
--   article_freshness_factor,
--   article_freshness_weight_active
-- FROM crawler_news_main
-- WHERE article_scored_at IS NOT NULL
-- ORDER BY article_overall_score DESC, id ASC;
