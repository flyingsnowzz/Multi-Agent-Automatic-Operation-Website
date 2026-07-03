-- Mark crawler articles that have already entered scoring so they are not reused.
-- Run against the MySQL database configured by MYSQL_DATABASE.

ALTER TABLE `crawler_news_main`
  ADD COLUMN `article_usage_status` VARCHAR(32) NULL COMMENT 'pipeline usage marker: used means already scored',
  ADD COLUMN `article_used_at` DATETIME NULL COMMENT 'time when the article was marked used by pipeline scoring',
  ADD INDEX `idx_article_usage_status` (`article_usage_status`),
  ADD INDEX `idx_article_used_at` (`article_used_at`);
