from __future__ import annotations

import re
from pathlib import Path
from typing import Any


GENERIC_TOKEN_LABELS = {
    "account": "账号",
    "activity": "活动",
    "amount": "金额",
    "avg": "平均",
    "average": "平均",
    "branch": "门店",
    "brand": "品牌",
    "budget": "预算",
    "campaign": "活动",
    "card": "卡券",
    "category": "品类",
    "channel": "渠道",
    "city": "城市",
    "collection": "资料集合",
    "contract": "合同",
    "cost": "成本",
    "count": "数量",
    "customer": "客户",
    "date": "日期",
    "day": "日期",
    "detail": "明细",
    "duration": "时长",
    "event": "活动",
    "first": "首次",
    "frequency": "频次",
    "goal": "目标",
    "group": "分组",
    "home": "归属",
    "id": "编号",
    "last": "最近",
    "level": "等级",
    "location": "位置",
    "member": "会员",
    "month": "月份",
    "name": "名称",
    "order": "订单",
    "plan": "方案",
    "price": "价格",
    "product": "产品",
    "purchase": "购买",
    "rate": "比例",
    "region": "区域",
    "renewal": "续费",
    "retention": "留存",
    "risk": "风险",
    "revenue": "收入",
    "score": "评分",
    "segment": "客群",
    "signal": "信号",
    "signals": "信号",
    "source": "来源",
    "spend": "消费",
    "status": "状态",
    "store": "门店",
    "support": "服务",
    "time": "时间",
    "topic": "主题",
    "type": "类型",
    "usage": "使用",
    "user": "用户",
    "value": "数值",
    "visit": "到访",
    "visits": "到访次数",
}

INTERNAL_FIELD_NAMES = {
    "chunk_id",
    "content_vector",
    "document_type",
    "raw_docs",
    "reference_images",
    "source_file",
    "workspace_id",
}

CUSTOMER_TERM_REPLACEMENTS = [
    (r"\bgenerated data product\b", "数据产品"),
    (r"\bsynthetic data\b", "合成数据"),
    (r"\bnot_yet_feasible\b", "暂不可行"),
    (r"\bdata_confirmed\b", "工作区证据"),
    (r"\bmarket_inferred\b", "市场推断"),
    (r"\bspeculative\b", "证据不足"),
    (r"\bconditional\b", "有条件可行"),
    (r"\bfeasible\b", "可行"),
    (r"\bpass\b", "通过"),
    (r"\brevise\b", "需要修订"),
    (r"\bmembership\b", "会员类型"),
    (r"\bschema\b", "字段结构"),
]

CATEGORICAL_FALLBACK_LABELS = [
    "业务类别",
    "资料分组",
    "运营标签",
    "客户标签",
    "场景标签",
]


def field_tokens(name: Any) -> list[str]:
    text = str(name or "").strip()
    if not text:
        return []
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [token.lower() for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", text) if token]


def friendly_label(name: Any, *, role: Any = None, value: Any = None, index: int | None = None) -> str:
    raw = str(name or "").strip()
    tokens = [token for token in field_tokens(raw) if token not in {"the", "a", "an"}]
    mapped: list[str] = []
    for token in tokens:
        label = GENERIC_TOKEN_LABELS.get(token)
        if label and label not in mapped:
            mapped.append(label)
    if mapped:
        return _compact_label("".join(mapped))

    role_text = str(role or "").lower()
    value_text = str(value or "").strip()
    if "date" in role_text or _looks_like_date(value_text):
        base = "时间维度"
    elif "number" in role_text or "int" in role_text or "float" in role_text or _looks_like_number(value_text):
        base = "数值指标"
    elif "bool" in role_text or value_text.lower() in {"true", "false", "yes", "no", "是", "否"}:
        base = "是否信息"
    elif value_text and len(value_text) > 36:
        base = "描述信息"
    else:
        if index and index > 0:
            return CATEGORICAL_FALLBACK_LABELS[(index - 1) % len(CATEGORICAL_FALLBACK_LABELS)]
        base = "业务类别"
    return base


def field_label_map_from_columns(columns: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for index, column in enumerate(columns, start=1):
        name = str(column.get("name") or "").strip()
        if not name:
            continue
        mapping[name] = friendly_label(
            name,
            role=column.get("role") or column.get("type"),
            value=_first_value(column.get("top_values")),
            index=index,
        )
    return mapping


def field_label_map_from_hits(hits: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    index = 1
    for hit in hits:
        content = str(hit.get("content") or "")
        for key, value in record_pairs(content):
            normalized = str(key).strip()
            if not normalized or normalized.lower() in INTERNAL_FIELD_NAMES:
                continue
            if normalized not in mapping:
                mapping[normalized] = friendly_label(normalized, value=value, index=index)
                index += 1
        for identifier in _identifier_candidates(content):
            if identifier not in mapping:
                mapping[identifier] = friendly_label(identifier, index=index)
                index += 1
    return mapping


def record_pairs(content: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in re.split(r";|\n", str(content or "")):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            pairs.append((key, value))
    return pairs


def sanitize_customer_text(text: Any, field_labels: dict[str, str] | None = None) -> str:
    value = str(text or "")
    value = re.sub(r"\[?(?:raw_docs|external)/[^\]\s,;，。；：）)]+#?[^\]\s,;，。；：）)]*\]?", "", value)
    value = re.sub(r"\[?profile\.json[^\]\s,;，。；：）)]*\]?", "", value)
    value = re.sub(r"\b(?:chunk_id|source_file|workspace_id|document_type|content_vector)\b", "", value)
    for pattern, replacement in CUSTOMER_TERM_REPLACEMENTS:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    for raw, label in sorted((field_labels or {}).items(), key=lambda item: len(item[0]), reverse=True):
        raw = str(raw or "").strip()
        label = str(label or "").strip()
        if len(raw) < 3 or not label:
            continue
        value = _replace_identifier(value, raw, label)
    value = re.sub(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*\b", _replace_leftover_identifier, value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\s+([，。；：、！？])", r"\1", value)
    value = re.sub(r"([。！？]){2,}", r"\1", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def sanitize_citations(citations: list[dict[str, Any]], field_labels: dict[str, str]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in citations:
        clone = dict(item)
        clone["snippet"] = sanitize_customer_text(clone.get("snippet"), field_labels)
        if clone.get("confidence") and not clone.get("confidence_label"):
            clone["confidence_label"] = sanitize_customer_text(str(clone.get("confidence")))
        source = str(clone.get("source_file") or "")
        if source:
            clone.setdefault("source_label", _source_label(source))
        cleaned.append(clone)
    return cleaned


def customer_hit_title(hit: dict[str, Any]) -> str:
    """Return a short customer-facing title for one retrieved hit."""
    field_labels = field_label_map_from_hits([hit])
    content = str(hit.get("content") or "")
    if str(hit.get("document_type") or "") == "profile":
        match = re.search(r"(?:高信号|交叉信号)[：:]\s*([^。\n；;]+)", content)
        if match:
            signal = sanitize_customer_text(match.group(1), field_labels)
            return _clip_title(f"数据画像显示：{signal}")
        return "数据画像中的关键线索"

    pairs = []
    skip = INTERNAL_FIELD_NAMES | {"id", "row", "source", "collection", "document_type"}
    for index, (name, value) in enumerate(record_pairs(content), start=1):
        lowered = name.lower().strip()
        if lowered in skip or not value or len(value) < 2:
            continue
        label = friendly_label(name, value=value, index=index)
        clean_value = _short_title_value(value)
        if not clean_value:
            continue
        if _looks_like_number(clean_value):
            pairs.append(f"{label}{clean_value}")
        else:
            pairs.append(f"{label}为{clean_value}")
        if len(pairs) >= 2:
            break
    if pairs:
        return _clip_title(sanitize_customer_text("、".join(pairs) + "的资料线索", field_labels))

    title = sanitize_customer_text(hit.get("title") or "", field_labels)
    if title and not re.search(r"(raw_docs|chunk|row-\d+)", title, flags=re.IGNORECASE):
        return _clip_title(title)
    return "工作区资料中的命中线索"


def customer_summary_from_profile(profile: dict[str, Any], meta: dict[str, Any] | None = None) -> str:
    tables = [table for table in profile.get("tables") or [] if isinstance(table, dict)]
    rows = sum(int(table.get("row_count") or 0) for table in tables)
    columns: list[dict[str, Any]] = []
    for table in tables:
        columns.extend([column for column in table.get("columns") or [] if isinstance(column, dict)])
    labels = []
    for column in columns:
        label = friendly_label(
            column.get("name"),
            role=column.get("type"),
            value=_first_value(column.get("top_values")),
            index=len(labels) + 1,
        )
        if label not in labels:
            labels.append(label)
        if len(labels) >= 6:
            break
    doc_count = len((meta or {}).get("documents") or profile.get("documents") or [])
    scope = f"共 {rows} 条记录" if rows else f"共 {doc_count or len(tables) or 1} 份资料"
    table_part = f"，覆盖 {len(tables)} 个表/集合" if len(tables) > 1 else ""
    label_part = "，可观察" + "、".join(labels) if labels else ""
    return f"{scope}{table_part}{label_part}，适合用来做资料问答、机会判断和下一步方案验证。"


def clarify_options_from_context(context: dict[str, Any], message: str = "") -> list[dict[str, str]]:
    summary = str(context.get("profile_summary") or "")
    seed = f"{message} {context.get('name') or ''} {summary}".lower()
    options: list[dict[str, str]] = []

    def add(option_id: str, label: str) -> None:
        if label and all(item["label"] != label for item in options):
            options.append({"id": option_id, "label": label[:40]})

    if re.search(r"活动|campaign|推广|promotion|event", seed):
        add("goal_campaign", "先做一版活动推广方案")
    if re.search(r"会员|customer|member|用户|复购|retention", seed):
        add("goal_retention", "优先提升老客复购或会员活跃")
    if re.search(r"新客|拉新|acquisition|新增", seed):
        add("goal_new_customer", "优先拉新和扩大触达")
    if re.search(r"成本|预算|cost|budget|low", seed):
        add("constraint_low_budget", "预算有限，优先低成本验证")
    if re.search(r"门店|store|branch|region|区域", seed):
        add("scope_store", "按门店或区域先做小范围试点")
    if re.search(r"产品|product|category|周边|权益", seed):
        add("scope_product", "围绕产品、权益或组合设计")

    add("output_plan", "我想要可执行的下一步方案")
    add("output_evidence", "我想先看资料里最强的证据")
    return options[:5]


def normalize_clarify_options(options: Any, context: dict[str, Any], message: str = "") -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if isinstance(options, list):
        for index, item in enumerate(options, start=1):
            if not isinstance(item, dict):
                continue
            label = sanitize_customer_text(item.get("label") or "")
            if not label:
                continue
            option_id = re.sub(r"[^a-z0-9_]+", "_", str(item.get("id") or f"option_{index}").lower()).strip("_")
            normalized.append({"id": option_id or f"option_{index}", "label": label[:40]})
            if len(normalized) >= 5:
                break
    if len(normalized) < 2:
        normalized = clarify_options_from_context(context, message)
    return normalized[:5]


def _identifier_candidates(content: str) -> list[str]:
    candidates: list[str] = []
    skip = {
        "csv",
        "json",
        "markdown",
        "md",
        "xlsx",
        "xls",
        "schema",
        "profile",
        "dataforge",
    }
    for raw in re.findall(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]{2,})(?![A-Za-z0-9_])", content):
        lowered = raw.lower()
        if lowered in skip or lowered in INTERNAL_FIELD_NAMES:
            continue
        if "_" not in raw:
            continue
        if raw not in candidates:
            candidates.append(raw)
    return candidates


def _replace_identifier(text: str, raw: str, label: str) -> str:
    escaped = re.escape(raw)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", raw):
        return re.sub(rf"(?<![A-Za-z0-9_.-]){escaped}(?![A-Za-z0-9_.-])", label, text)
    return text.replace(raw, label)


def _replace_leftover_identifier(match: re.Match[str]) -> str:
    value = match.group(0)
    lowered = value.lower()
    if "batch" in lowered or lowered.endswith(("signals", "records", "metrics", "table")):
        return "资料集合"
    return "数据字段"


def _compact_label(label: str) -> str:
    label = re.sub(r"(信息|维度|指标){2,}", r"\1", label)
    label = re.sub(r"(.{2,4})\1+", r"\1", label)
    return label[:16] or "资料维度"


def _first_value(values: Any) -> str:
    if isinstance(values, list) and values:
        first = values[0]
        if isinstance(first, dict):
            return str(first.get("value") or first.get("label") or "")
        return str(first)
    return ""


def _looks_like_number(value: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", value.strip()))


def _looks_like_date(value: str) -> bool:
    return bool(re.search(r"\b20\d{2}[-/年]\d{1,2}", value))


def _source_label(source: str) -> str:
    name = Path(source.replace("\\", "/")).name
    if not name or name == "profile.json":
        return "数据画像"
    stem = re.sub(r"[_-]+", " ", Path(name).stem).strip()
    mapped: list[str] = []
    for token in field_tokens(stem):
        if token.startswith("batch") or token.isdigit():
            continue
        label = GENERIC_TOKEN_LABELS.get(token)
        if label and label not in mapped:
            mapped.append(label)
    if mapped:
        return _compact_label("".join(mapped) + "资料")
    return sanitize_customer_text(stem)[:40] or "工作区资料"


def _short_title_value(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ，。；;")
    if not text:
        return ""
    text = re.sub(r"\b(?:raw_docs|profile|chunk|row-\d+)\b", "", text, flags=re.IGNORECASE).strip()
    if len(text) > 18:
        text = text[:18].rstrip() + "..."
    return text


def _clip_title(text: str, limit: int = 34) -> str:
    clean = sanitize_customer_text(text)
    clean = re.sub(r"[。；;]+$", "", clean)
    if len(clean) <= limit:
        return clean or "工作区资料中的命中线索"
    head = clean[:limit]
    for sep in ("，", "、", "：", " "):
        pos = head.rfind(sep)
        if pos >= int(limit * 0.55):
            return head[:pos].rstrip("，、： ") + "..."
    return head.rstrip() + "..."
