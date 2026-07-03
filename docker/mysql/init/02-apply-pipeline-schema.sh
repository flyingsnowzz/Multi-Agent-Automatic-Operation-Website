#!/bin/sh
set -eu

echo "[mysql-init] applying pipeline schema"

mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" < /docker-sql/create_pipeline_audit.sql
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" < /docker-sql/alter_pipeline_audit_seo_meta.sql
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" < /docker-sql/alter_pipeline_audit_drop_large_audit_payloads.sql
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" < /docker-sql/create_research_article_candidates.sql
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" < /docker-sql/create_writer_article_outputs.sql

echo "[mysql-init] pipeline schema ready"
