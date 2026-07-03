-- Pipeline audit trail: per-article metadata from each stage
USE multi_agent_cms;

CREATE TABLE IF NOT EXISTS pipeline_audit (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id BIGINT UNSIGNED NOT NULL COMMENT 'crawler_news_main.id',
  ai_score DECIMAL(5,2) NULL,
  quality_score DECIMAL(5,2) NULL,
  rewrite_quality_after DECIMAL(5,2) NULL,
  generated_title VARCHAR(512) NULL COMMENT 'WriterAgent 生成标题',
  generated_content_md LONGTEXT NULL COMMENT 'WriterAgent 生成正文 Markdown',
  edited_title VARCHAR(512) NULL COMMENT 'EditorAgent 编辑后标题',
  edited_content_md LONGTEXT NULL COMMENT 'EditorAgent 编辑后正文 Markdown',
  image_url TEXT NULL COMMENT 'ImageAgent 原始图片 URL',
  image_local_path TEXT NULL COMMENT 'ImageAgent 本地图片路径',
  seo_meta_title VARCHAR(512) NULL COMMENT 'SEO meta title',
  seo_meta_description TEXT NULL COMMENT 'SEO meta description',
  seo_keywords JSON NULL,
  cms_status VARCHAR(32) NULL,
  cms_article_id VARCHAR(100) NULL COMMENT 'CMS 返回的文章 ID',
  cms_article_url TEXT NULL COMMENT 'CMS 返回的文章 URL',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_article_id (article_id),
  KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Pipeline 审计追踪';
