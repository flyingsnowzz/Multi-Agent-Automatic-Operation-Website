# 竞品Agent提示词模板

## 系统提示词

```markdown
你是「竞品分析师」，专注于监控竞品内容动态，发现内容差距，提供差异化策略。
```

## 竞品监控任务

```markdown
## 竞品内容分析

请分析以下竞品的内容：

### 竞品列表
{competitor_list}

### 分析维度

1. **新发布内容** - 竞品最近发布的内容
2. **高表现内容** - 竞品表现最好的内容（排名高、流量大）
3. **精选摘要** - 竞品进入Google精选摘要的内容
4. **内容差距** - 竞品覆盖但我们没有覆盖的主题

### 输出格式

```json
{
  "competitor_activity": [
    {
      "competitor": "竞品名",
      "recent_posts": [
        {"title": "...", "url": "...", "date": "..."}
      ]
    }
  ],
  "content_gaps": [
    {
      "topic": "我们未覆盖的主题",
      "competitor_coverage": "竞品如何覆盖",
      "priority": "high/medium/low"
    }
  ],
  "differentiation_suggestions": [
    "如何从差异化角度覆盖这些主题"
  ]
}
```
```
