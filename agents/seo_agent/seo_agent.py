"""
SEOAgent 主入口。

这个文件的角色不是“把所有 SEO 逻辑都写在这里”，而是做三件事：
1. 读取配置、品牌信息、LLM 参数
2. 组织各个工具模块（关键词分析 / Meta 生成 / Schema 生成 / DB 读取）
3. 对外暴露统一的执行入口 `execute()` / `execute_from_db()`

理解这个文件时，可以把它看成一个“编排器”：
- 关键词分析：决定文章当前关键词布局是否合理
- Meta 生成：产出 meta title / description / OG / Twitter 标签
- Schema 生成：产出结构化数据 JSON-LD
- 最后把这些结果重新组装成一个统一的 SEO 输出对象
"""

import os, re, asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import yaml
from agents.seo_agent.tools.schema_generator import SchemaGenerator as _SchemaGenerator

def _deep_env_resolve(value: Any) -> Any:
    """递归展开配置中的环境变量写法。

    支持：
    - `${KEY}`：直接读取环境变量
    - `${KEY:-default}`：环境变量不存在时使用默认值

    这个函数的意义是让 `config.yaml` 可以安全地引用密钥和环境差异配置，
    避免把 API Key 等敏感信息硬编码到仓库里。
    """
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            expr = value[2:-1]
            if ":-" in expr:
                key, default = expr.split(":-", 1)
                return os.environ.get(key, default)
            return os.environ.get(expr, "")
        return value
    if isinstance(value, dict): return {k: _deep_env_resolve(v) for k, v in value.items()}
    if isinstance(value, list): return [_deep_env_resolve(v) for v in value]
    return value

def _word_count(text: str) -> int:
    """粗略统计词数。

    这里没有做非常严格的中英文分词，而是采取项目里够用的估算方式：
    - 中文：按汉字个数统计
    - 英文：按单词统计

    用途主要是：
    - 输出 SEO 报告中的 `word_count`
    - 给 Schema `wordCount` 字段提供基础值
    """
    s = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", s)) + len(re.findall(r"\b[a-zA-Z]+\b", s))

@dataclass
class KeywordResult:
    """关键词分析的标准结果对象。

    为什么要单独定义 dataclass：
    - 不同关键词分析器（v1 / v2）输出字段不完全一样
    - 这里先把它们归一化成统一结构，后续组装 SEO 结果时更简单
    """
    keywords: List[str]; density: float; occurrences: int; total_words: int
    distribution: Dict[str, Any]; assessment: Dict[str, Any]; analyzer: str

@dataclass
class MetaResult:
    """Meta 标签生成的标准结果对象。

    同样用于屏蔽不同 Meta 生成器（规则版 / LLM 版）的细节差异。
    """
    meta_title: str; meta_description: str; title_length: int; description_length: int
    reasoning: Dict[str, Any]; og_tags: Dict[str, str]; twitter_tags: Dict[str, str]; model_used: str

class SEOAgent:
    def __init__(self, config_path="agents/seo_agent/config.yaml", brand_path="config/brand_guidelines.yaml", mode="v2"):
        """初始化 SEOAgent。

        参数说明：
        - config_path：SEO Agent 自己的配置文件，决定模型、阈值、Meta 长度等
        - brand_path：品牌配置文件，主要用来读取品牌名，拼到 Meta Title / OG 中
        - mode：关键词分析模式，`v1` 是规则版，`v2` 是 LLM 版
        """
        self.config_path, self.brand_path, self.mode = config_path, brand_path, mode.strip().lower()
        self.config = self._load_config()

    def _config_file(self):
        """优先使用外部传入路径；不存在时回退到当前 agent 自带的 config.yaml。"""
        p = self.config_path.strip()
        return p if os.path.exists(p) else str(Path(__file__).resolve().parent / "config.yaml")
    def _brand_file(self):
        """定位品牌配置文件；如果外部路径不存在，则回退到项目级 brand_guidelines.yaml。"""
        p = self.brand_path.strip()
        return p if os.path.exists(p) else str(Path(__file__).resolve().parents[2] / "config" / "brand_guidelines.yaml")
    def _load_config(self):
        """加载 SEO Agent 配置，并做环境变量展开。"""
        p = self._config_file()
        if not os.path.exists(p): return {}
        with open(p,"r",encoding="utf-8") as f: return _deep_env_resolve(yaml.safe_load(f) or {})
    def _brand_name(self):
        """读取品牌名。

        Meta Title、OG 标签、Twitter 标签都可能需要品牌名。
        如果品牌配置缺失，就回退到默认品牌，避免生成阶段直接报错。
        """
        p = self._brand_file()
        if os.path.exists(p):
            with open(p,"r",encoding="utf-8") as f:
                r = _deep_env_resolve(yaml.safe_load(f) or {})
                if isinstance(r, dict): return str(r.get("brand_name") or "").strip()
        return "TechAI Insight"
    def _llm_config(self):
        """汇总 LLM 配置。

        优先级设计：
        1. 环境变量（便于部署时覆盖）
        2. config.yaml
        3. 默认值

        这样既方便本地调试，也方便线上按环境切换模型。
        """
        c = (self.config or {}).get("llm") if isinstance(self.config, dict) else {}
        return {
            "model": os.environ.get("SEO_AGENT_MODEL") or c.get("model") or "deepseek-chat",
            "base_url": os.environ.get("SEO_AGENT_BASE_URL") or c.get("base_url") or os.environ.get("OPENAI_BASE_URL"),
            "api_key": os.environ.get("SEO_AGENT_API_KEY") or c.get("api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "",
        }
    def _resolve_mode(self, requested=None):
        """解析关键词分析模式，只允许 v1 / v2。"""
        m = (requested or self.mode or "v2").strip().lower()
        return m if m in ("v1","v2") else "v2"
    def _derive_primary_keyword(self, *, title, topic):
        """推导主关键词。

        优先级：
        1. topic.primary_keyword
        2. topic.target_keywords 的第一个
        3. 退化为 title

        这样即使上游 TopicAgent 没有明确给出主关键词，SEOAgent 也能继续工作。
        """
        pk = str((topic or {}).get("primary_keyword") or "").strip()
        if pk: return pk
        tks = (topic or {}).get("target_keywords")
        if isinstance(tks, list) and tks: return str(tks[0] or "").strip()
        return title.strip()

    def _analyze_keywords_v1(self, content, primary_keyword):
        """调用传统规则版关键词分析器，并归一化成 KeywordResult。"""
        from agents.seo_agent.tools.keyword_analyzer_v1 import KeywordAnalyzerV1
        a = KeywordAnalyzerV1(config_path=self._config_file())
        r = a.analyze(content=content, target_keyword=primary_keyword)
        return KeywordResult(keywords=r.get("keywords",[]) or [r["primary_keyword"]]+r.get("candidates",[])[:7],
            density=r.get("density",0.0), occurrences=r.get("occurrences",0),
            total_words=r.get("total_words",0), distribution=r.get("distribution",{}), assessment=r.get("assessment",{}),
            analyzer=r.get("analyzer","v1_traditional"))

    async def _analyze_keywords_v2_async(self, content, primary_keyword):
        """调用 LLM 版关键词分析器，并归一化成 KeywordResult。"""
        from agents.seo_agent.tools.keyword_analyzer_v2 import KeywordAnalyzerV2
        llm = self._llm_config()
        a = KeywordAnalyzerV2(model=llm["model"], base_url=llm["base_url"], api_key=llm["api_key"])
        r = await a.analyze(content=content, target_keyword=primary_keyword)
        return KeywordResult(keywords=r.get("keywords",[]) or [r.get("primary_keyword",primary_keyword)]+r.get("secondary_keywords",[]),
            density=float(r.get("keyword_density") or 0),
            occurrences=0, total_words=_word_count(content), distribution=r.get("distribution",{}), assessment=r.get("assessment",{}),
            analyzer=r.get("analyzer","v2_llm"))

    async def _generate_meta_async(self, title, content, primary_keyword):
        """调用 LLM Meta 生成器，并归一化成 MetaResult。"""
        from agents.seo_agent.tools.meta_generator_llm import MetaGeneratorLLM
        llm = self._llm_config(); brand = self._brand_name()
        g = MetaGeneratorLLM(brand_name=brand, model=llm["model"], base_url=llm["base_url"], api_key=llm["api_key"], config_path=self._config_file())
        r = await g.generate(title=title, content=content, primary_keyword=primary_keyword)
        mt, md = r.get("meta_title",title), r.get("meta_description","")
        return MetaResult(meta_title=mt, meta_description=md, title_length=int(r.get("title_length",0)),
            description_length=int(r.get("description_length",0)), reasoning=r.get("reasoning",{}),
            og_tags={"og:title":mt,"og:description":md,"og:type":"article","og:site_name":brand},
            twitter_tags={"twitter:card":"summary_large_image","twitter:title":mt,"twitter:description":md},
            model_used=r.get("model_used",llm["model"]))

    def _schema_type(self, topic):
        """根据 topic 内容类型映射到 Schema 类型。

        如果上游没传合法类型，就默认走 article。
        这样可以保证 Schema 生成阶段稳定运行。
        """
        t = str((topic or {}).get("content_type") or (topic or {}).get("category") or "").strip().lower()
        return t if t in {"blog","news","faq","howto","breadcrumb","article"} else "article"

    def _normalize_suggestions(self, s):
        """把建议字段统一整理成字符串数组。

        由于不同工具返回的建议字段可能是：
        - None
        - 单个字符串
        - 数组
        - 其他对象
        这里做统一归一化，便于前端或下游工作流直接消费。
        """
        if s is None: return []
        if isinstance(s, list): return [str(x) for x in s if str(x or "").strip()]
        if isinstance(s, str): return [s.strip()] if s.strip() else []
        return [str(s)]

    async def execute(self, *, article, topic=None, page_info=None, dry_run=True, language=None, keyword_mode=None):
        """SEOAgent 的主执行入口。

        输入：
        - article：文章内容（通常来自 Writer / Editor）
        - topic：主题信息（主关键词、分类、内容类型等）
        - page_info：页面相关信息（URL、作者、发布者等）

        执行流程：
        1. 规范化输入
        2. 推导主关键词
        3. 进行关键词分析（v1/v2）
        4. 生成 Meta 标签
        5. 生成 Schema
        6. 把所有结果组装成统一输出

        注意：
        - 这里不会直接改写正文内容，只是为文章补充 SEO 相关元数据
        - `dry_run` 当前主要是接口兼容占位，方便未来扩展成真实发布前检查模式
        """
        article = article if isinstance(article, dict) else {}
        topic = topic if isinstance(topic, dict) else {}
        page_info = page_info if isinstance(page_info, dict) else {}
        title = str(article.get("title") or topic.get("title") or "")
        content = str(article.get("content_md") or article.get("content") or article.get("content_html") or "")
        primary_keyword = self._derive_primary_keyword(title=title, topic=topic)
        mode = self._resolve_mode(keyword_mode)
        # 关键词分析是 SEO 的起点：
        # - v1：规则、低成本、稳定
        # - v2：LLM、语义更强、成本更高
        if mode == "v2": kw = await self._analyze_keywords_v2_async(content, primary_keyword)
        else: kw = self._analyze_keywords_v1(content, primary_keyword)

        # Meta 标签单独生成，而不是混在关键词分析里，
        # 因为这一步更偏“点击率优化”和“搜索结果展示优化”。
        meta = await self._generate_meta_async(title, content, primary_keyword)
        sg = _SchemaGenerator(config_path=self._config_file(), brand_path=self._brand_file())
        st = self._schema_type(topic)

        # Schema 输入字段做了一层映射，
        # 因为 SchemaGenerator 希望收到的是更标准化的 article 对象。
        kfs = [((kw.keywords or [""])[0] if kw.keywords else "")] + [k for k in kw.keywords[:8] if k]
        sa = {"title":title,"meta_description":meta.meta_description,"url":page_info.get("url") or article.get("url") or "",
              "published_date":article.get("published_date") or "", "modified_date":article.get("modified_date") or "",
              "category":topic.get("category") or "", "keywords":kfs, "word_count":_word_count(content),
              "publisher":page_info.get("publisher") or "", "author":page_info.get("author") or ""}
        schema_json = sg.generate(sa, schema_type=st)
        keyword_result = {
            "keywords": kw.keywords,
            "density": kw.density,
            "occurrences": kw.occurrences,
            "total_words": kw.total_words,
            "distribution": kw.distribution,
            "assessment": kw.assessment,
            "analyzer": kw.analyzer,
        }
        warnings = []
        if not content.strip():
            warnings.append("content_empty")
        if not primary_keyword.strip():
            warnings.append("primary_keyword_empty")

        # 返回值尽量保持“工作流友好”：
        # - optimized_article：给 CMS / 下游使用
        # - keyword_result / seo_report：给运营、日志、调试使用
        # - schema_json / meta_*：给渲染层或发布层使用
        return {
            "optimized_article": {
                **article,
                "title": title,
                "content_md": content,
                "slug": article.get("slug") or page_info.get("slug") or "",
                "meta": {"meta_title": meta.meta_title, "meta_description": meta.meta_description},
            },
            "keyword_result": keyword_result,
            "meta_title": meta.meta_title,
            "meta_description": meta.meta_description,
            "og_tags": meta.og_tags,
            "twitter_tags": meta.twitter_tags,
            "schema_json": schema_json,
            "internal_links": [],
            "seo_report": {
                "primary_keyword": primary_keyword,
                "keyword_mode": mode,
                "word_count": _word_count(content),
                "keyword_density": kw.density,
                "model_used": meta.model_used,
            },
            "improvement_suggestions": self._normalize_suggestions(kw.assessment.get("suggestions") if isinstance(kw.assessment, dict) else []),
            "warnings": warnings,
            "generated_at": datetime.now().isoformat(),
        }

    async def execute_from_db(self, *, article_id=None, candidate_id=None, limit=10, min_score=70.0, keyword_mode=None):
        """从数据库批量读取 Writer 产物并执行 SEO。

        这个入口适合“离线批处理”场景：
        - Writer 已经写完并存库
        - 现在需要批量补 SEO 元信息

        注意：
        - 这里只读取 DB 并调用 `execute`
        - 不负责把 SEO 结果再写回数据库，写回逻辑通常放在上层 workflow / service 中
        """
        from agents.seo_agent.tools.db_reader import ArticleDBReader
        reader = ArticleDBReader()
        if article_id is not None: records = [reader.fetch_by_id(article_id)] if reader.fetch_by_id(article_id) else []
        else: records = reader.fetch_generated(limit=limit, min_score=min_score, candidate_id=candidate_id)
        results = []
        for rec in records:
            r = await self.execute(article={"title":rec.generated_title or rec.source_title or "","content_md":rec.generated_content_md or ""},
                                   topic={"title":rec.generated_title or "","primary_keyword":""}, page_info={}, dry_run=True, keyword_mode=keyword_mode)
            r["db_article_id"], r["db_candidate_id"] = rec.id, rec.candidate_id
            results.append(r)
        return results

    def execute_sync(self, **kwargs):
        """同步包装器。

        由于 `execute()` 是 async，命令行或脚本直调时不方便。
        这里提供一个同步入口：
        - 如果当前已经在事件循环中，直接报错提示应使用 `await`
        - 否则内部用 `asyncio.run()` 执行
        """
        try: asyncio.get_running_loop(); raise RuntimeError("Use await seo_agent.execute(...) instead")
        except RuntimeError as e:
            if "Use await" in str(e): raise
            return asyncio.run(self.execute(**kwargs))
