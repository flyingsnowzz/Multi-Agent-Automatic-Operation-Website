#!/usr/bin/env python3
"""
数据库初始化脚本
创建所需的数据库表结构
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def init_database():
    load_dotenv()
    
    # 构建数据库连接URL
    db_user = os.environ.get("POSTGRES_USER", "postgres")
    db_password = os.environ.get("POSTGRES_PASSWORD", "password")
    db_host = os.environ.get("POSTGRES_HOST", "localhost")
    db_port = os.environ.get("POSTGRES_PORT", "5432")
    db_name = os.environ.get("POSTGRES_DB", "multi_agent_cms")
    
    # 先连接到默认数据库创建目标数据库
    default_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/postgres"
    
    try:
        # 创建目标数据库（如果不存在）
        default_engine = create_engine(default_url)
        with default_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {db_name}"))
            conn.commit()
        default_engine.dispose()
        
        # 连接到目标数据库创建表
        db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        engine = create_engine(db_url)
        
        create_tables_sql = """
        CREATE TABLE IF NOT EXISTS topics (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(500) NOT NULL,
            target_keywords TEXT[],
            search_volume INTEGER,
            difficulty INTEGER,
            intent_type VARCHAR(20),
            content_type VARCHAR(30),
            outline JSONB,
            priority INTEGER DEFAULT 3,
            status VARCHAR(20) DEFAULT 'pending',
            source VARCHAR(50),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            approved_at TIMESTAMPTZ,
            approved_by VARCHAR(100)
        );

        CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);
        CREATE INDEX IF NOT EXISTS idx_topics_priority ON topics(priority DESC);

        CREATE TABLE IF NOT EXISTS articles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            topic_id UUID REFERENCES topics(id),
            title VARCHAR(500) NOT NULL,
            slug VARCHAR(300) UNIQUE,
            content_md TEXT,
            content_html TEXT,
            word_count INTEGER,
            quality_score DECIMAL(5,2),
            seo_score DECIMAL(5,2),
            target_keyword VARCHAR(200),
            meta_title VARCHAR(200),
            meta_desc VARCHAR(500),
            schema_json JSONB,
            status VARCHAR(20) DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            published_url VARCHAR(500),
            cms_post_id VARCHAR(100),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
        CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_articles_keyword ON articles(target_keyword);

        CREATE TABLE IF NOT EXISTS tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workflow_id UUID,
            agent_name VARCHAR(50) NOT NULL,
            task_type VARCHAR(30),
            input_data JSONB,
            output_data JSONB,
            status VARCHAR(20) DEFAULT 'pending',
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent_name);

        CREATE TABLE IF NOT EXISTS workflows (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100),
            trigger_type VARCHAR(30),
            trigger_config JSONB,
            state JSONB,
            current_stage VARCHAR(50),
            is_running BOOLEAN DEFAULT FALSE,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS config (
            key VARCHAR(255) PRIMARY KEY,
            value JSONB,
            category VARCHAR(50)
        );
        """
        
        with engine.connect() as conn:
            for stmt in create_tables_sql.split(';'):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
            conn.commit()
        
        engine.dispose()
        print("✅ 数据库表创建成功")
        return True
        
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        return False

if __name__ == "__main__":
    init_database()