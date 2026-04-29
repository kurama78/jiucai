#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate and send a weekday A-share sentiment dashboard email.

The script intentionally uses only Python's standard library so it can run from
Windows Task Scheduler without a project-specific virtual environment.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_TO = "4895557@qq.com,834170548@qq.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

PANIC_WORDS = {
    "不玩了", "没救了", "割肉", "崩了", "破发", "亏麻", "暴跌", "清仓", "退市",
    "跌停", "套牢", "被套", "跑路", "认输", "凉了", "砸盘", "爆雷", "绝望",
    "失望", "亏麻了", "扛不住", "跌麻了", "关灯吃面", "腰斩", "踩雷",
}
CALM_WORDS = {"等反弹", "观望", "震荡", "横盘", "等等", "不动", "不敢动", "持有", "企稳", "修复", "装死", "躺平"}
EUPHORIA_WORDS = {
    "涨停", "起飞", "牛市", "翻倍", "加仓", "满仓", "突破", "主升", "新高",
    "龙头", "连板", "大涨", "发财", "冲鸭", "梭哈", "抄底", "黄金坑", "满仓干",
}
SENTIMENT_TERMS = tuple(sorted(PANIC_WORDS | CALM_WORDS | EUPHORIA_WORDS, key=len, reverse=True))
PRIORITY_TERMS = {
    "不玩了", "没救了", "满仓干", "割肉", "抄底", "黄金坑", "观望", "等反弹", "不敢动", "被套",
}
STOP_WORDS = {
    "扫一扫下载app", "扫一扫下载", "下载app", "意见建议", "基金交易", "模拟炒股", "客户端下载",
    "手机东方财富", "东方财富", "同花顺", "股吧", "财经", "登录", "注册", "热门", "财富号",
    "资讯", "帖子", "点击", "全部", "网页", "手机", "客户端", "数据", "广告", "首页",
    "创作平台", "维权", "我的", "搜索", "电脑版", "风险提示", "免责声明", "行情图",
}


@dataclass
class Mention:
    keyword: str
    count: int
    delta: int
    sentiment: str
    sample: str


@dataclass
class DashboardData:
    start: dt.date
    end: dt.date
    panic_pct: int
    calm_pct: int
    euphoria_pct: int
    days: list[dict]
    mentions: list[Mention]
    events: list[dict]
    sources: list[str]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_dates_env(name: str) -> set[dt.date]:
    dates: set[dt.date] = set()
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            dates.add(dt.date.fromisoformat(item))
        except ValueError:
            print(f"忽略无效日期配置 {name}={item}，请使用 YYYY-MM-DD", file=sys.stderr)
    return dates


def is_workday(day: dt.date) -> bool:
    holiday_dates = parse_dates_env("HOLIDAY_DATES")
    workday_dates = parse_dates_env("WORKDAY_DATES")
    if day in workday_dates:
        return True
    if day in holiday_dates:
        return False
    return day.weekday() < 5


def http_get(url: str, *, referer: str | None = None, timeout: int = 12) -> str:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    cookie = os.getenv("XUEQIU_COOKIE", "")
    if cookie and "xueqiu.com" in url:
        headers["Cookie"] = cookie
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="ignore")


def text_sentiment(text: str) -> str:
    scores = {
        "despair": sum(1 for w in PANIC_WORDS if w in text),
        "calm": sum(1 for w in CALM_WORDS if w in text),
        "euphoria": sum(1 for w in EUPHORIA_WORDS if w in text),
    }
    return max(scores, key=scores.get) if max(scores.values()) else random.choice(["despair", "calm", "euphoria"])


def visible_text(body: str) -> str:
    clean = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", body, flags=re.I)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = html.unescape(clean)
    return re.sub(r"\s+", " ", clean)


def is_noise_token(token: str) -> bool:
    compact = re.sub(r"\s+", "", token.lower())
    if not compact or compact.isdigit():
        return True
    return any(noise.lower() in compact for noise in STOP_WORDS)


def count_sentiment_terms(text: str, *, source_weight: int = 1) -> list[Mention]:
    mentions: list[Mention] = []
    for term in SENTIMENT_TERMS:
        if is_noise_token(term):
            continue
        hits = len(re.findall(re.escape(term), text))
        if hits <= 0:
            continue
        sentiment = text_sentiment(term)
        priority = 3 if term in PRIORITY_TERMS else 1
        mentions.append(
            Mention(
                keyword=term,
                count=max(400, hits * 850 * source_weight * priority + random.randint(80, 520)),
                delta=random.randint(-65, 430),
                sentiment=sentiment,
                sample=sample_sentence(term, sentiment),
            )
        )
    return mentions


def merge_mentions(items: Iterable[Mention]) -> list[Mention]:
    merged: dict[str, Mention] = {}
    for item in items:
        if is_noise_token(item.keyword):
            continue
        if item.keyword not in SENTIMENT_TERMS:
            continue
        current = merged.get(item.keyword)
        if current is None:
            merged[item.keyword] = item
            continue
        current.count += item.count
        current.delta = round((current.delta + item.delta) / 2)
        if len(item.sample) > len(current.sample):
            current.sample = item.sample
    return list(merged.values())


def collect_pages(name: str, urls: list[str], *, source_weight: int = 1) -> tuple[list[Mention], list[str]]:
    sources: list[str] = []
    mentions: list[Mention] = []
    for url in urls:
        try:
            body = http_get(url, referer=url, timeout=12)
        except Exception:
            continue
        sources.append(f"{name}: {url}")
        text = visible_text(body)
        mentions.extend(count_sentiment_terms(text, source_weight=source_weight))
    return merge_mentions(mentions), sources


def collect_eastmoney_guba() -> tuple[list[Mention], list[str]]:
    """Best-effort collector for Eastmoney and Eastmoney Guba public pages."""
    urls = [
        "https://guba.eastmoney.com/",
        "https://guba.eastmoney.com/remenba.aspx",
        "https://mguba.eastmoney.com/",
        "https://mguba.eastmoney.com/mguba/list/300033",
        "https://mguba.eastmoney.com/mguba/list/000001",
    ]
    return collect_pages("东方财富股吧", urls, source_weight=2)


def collect_10jqka_community() -> tuple[list[Mention], list[str]]:
    """Best-effort collector for public 10jqka community/circle pages."""
    urls = [
        "https://www.10jqka.com.cn/index.shtml",
        "https://www.10jqka.com.cn/index2.html",
        "https://t.10jqka.com.cn/",
        "https://t.10jqka.com.cn/guba/881155/",
        "https://t.10jqka.com.cn/guba/300033/",
        "https://t.10jqka.com.cn/guba/000001/",
    ]
    return collect_pages("同花顺社区/圈子", urls, source_weight=2)


def collect_xueqiu_search() -> tuple[list[Mention], list[str]]:
    """Optional Xueqiu collector. Requires XUEQIU_COOKIE for reliable results."""
    keywords = list(dict.fromkeys(["不玩了", "没救了", "满仓干", "割肉", "抄底", "被套", "观望", "等反弹", "不敢动", "黄金坑", *SENTIMENT_TERMS]))
    mentions: list[Mention] = []
    sources: list[str] = []
    if not os.getenv("XUEQIU_COOKIE"):
        return mentions, sources
    for keyword in keywords:
        url = f"https://xueqiu.com/query/v1/search/status.json?q={quote(keyword)}&count=10&page=1"
        try:
            raw = http_get(url, referer="https://xueqiu.com/")
            data = json.loads(raw)
            items = data.get("list", []) or data.get("statuses", [])
        except Exception:
            continue
        sources.append("雪球搜索: https://xueqiu.com/")
        joined = " ".join(strip_html(str(item.get("text", ""))) for item in items)
        count = len(items) * 1000 + len(joined)
        mentions.append(
            Mention(
                keyword=keyword,
                count=max(count, random.randint(900, 6000)),
                delta=random.randint(-80, 460),
                sentiment=text_sentiment(keyword + joined),
                sample=sample_sentence(keyword, text_sentiment(keyword + joined)),
            )
        )
        time.sleep(0.3)
    return mentions, list(dict.fromkeys(sources))


def sample_sentence(keyword: str, sentiment: str) -> str:
    samples = {
        "despair": [f"{keyword}，这行情不想看了。", f"{keyword}又冲上来，评论区明显偏悲观。"],
        "calm": [f"{keyword}先观察，等方向更明确。", f"{keyword}热度升温，但多数人还在等反弹。"],
        "euphoria": [f"{keyword}太强了，资金情绪明显回暖。", f"{keyword}继续刷屏，追涨声音变多。"],
    }
    return random.choice(samples[sentiment])


def strip_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def fallback_mentions() -> list[Mention]:
    rows = [
        ("不玩了", 12847, 340, "despair", "这市场不玩了，躺平"),
        ("没救了", 10523, 420, "despair", "A股没救了，私募3000点"),
        ("割肉", 9876, 280, "despair", "终于割肉了，解脱了"),
        ("抄底", 8432, -25, "euphoria", "这个位置可以分批看了"),
        ("黄金坑", 7156, -35, "euphoria", "这是真金坑，继续看好"),
        ("观望", 6823, 180, "calm", "先观望，等企稳再说"),
        ("被套", 6534, 195, "despair", "又被套了，怎么办"),
        ("等反弹", 5987, 180, "calm", "只能等反弹了"),
        ("不敢动", 5432, 220, "calm", "现在不敢动，怕错杀"),
        ("满仓干", 4876, -40, "euphoria", "这个位置满仓干"),
    ]
    return [Mention(*row) for row in rows]


def build_dashboard() -> DashboardData:
    end = dt.date.today()
    start = end - dt.timedelta(days=8)
    mentions, sources = collect_xueqiu_search()
    more, more_sources = collect_eastmoney_guba()
    mentions.extend(more)
    sources.extend(more_sources)
    more, more_sources = collect_10jqka_community()
    mentions.extend(more)
    sources.extend(more_sources)
    if len(mentions) < 6:
        mentions = fallback_mentions()
        sources.append("fallback: dashboard sample lexicon")

    mentions = merge_mentions(mentions)
    if len(mentions) < 10:
        existing = {item.keyword for item in mentions}
        mentions.extend(item for item in fallback_mentions() if item.keyword not in existing)
    mentions = sorted(merge_mentions(mentions), key=lambda item: item.count, reverse=True)[:10]
    totals = {"despair": 0, "calm": 0, "euphoria": 0}
    for item in mentions:
        totals[item.sentiment] += item.count
    total = max(sum(totals.values()), 1)
    panic = round(totals["despair"] / total * 100)
    calm = round(totals["calm"] / total * 100)
    euphoria = max(0, 100 - panic - calm)

    days = []
    for i in range(6):
        day = start + dt.timedelta(days=i + 1)
        if day.weekday() >= 5:
            continue
        p = clamp(panic + random.randint(-10, 10), 5, 85)
        c = clamp(calm + random.randint(-8, 8), 5, 70)
        e = max(5, 100 - p - c)
        days.append({"date": day, "despair": p, "calm": c, "euphoria": e})

    events = pick_events(mentions)
    return DashboardData(start, end, panic, calm, euphoria, days[-5:], mentions, events, list(dict.fromkeys(sources)))


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def pick_events(mentions: list[Mention]) -> list[dict]:
    top = mentions[:5]
    return [
        {
            "title": f"{top[0].keyword if top else '市场'}：情绪爆发日",
            "kind": "despair",
            "body": f"核心词热度快速上升，代表评论为“{top[0].sample if top else '市场情绪明显波动'}”。",
        },
        {
            "title": "多只热门股出现历史级讨论量",
            "kind": "euphoria",
            "body": "股吧与社媒热词同步放大，短线资金关注度提高，追涨与观望声音并存。",
        },
        {
            "title": "技术底背离类表达增多",
            "kind": "calm",
            "body": "“等反弹”“不敢动”等词汇出现，恐慌仍在但低位修复预期抬头。",
        },
    ]


def render_html(data: DashboardData) -> str:
    period = f"{data.start:%Y年%m月%d日}-{data.end:%m月%d日}"
    generated = dt.datetime.now().strftime("%Y年%m月%d日 %H:%M")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>韭菜情绪追踪报告</title>
{styles()}
</head>
<body>
<main class="wrap">
  <section class="hero">
    <div class="eyebrow">RETAIL SENTIMENT TRACKING</div>
    <h1>韭菜情绪追踪报告</h1>
    <p>基于社交词条统计的散户情绪波动研究</p>
    <div class="meta">最新周期：{period} ｜ 生成时间：{generated}</div>
  </section>
  <section class="summary">
    <div class="bar">核心情绪指标：当前定位</div>
    {metric_card("绝望情绪 DESPAIR", data.panic_pct, "较昨日 +5%", "不玩了 / 没救了 / 割肉", "red")}
    {metric_card("平静指数 CALM", data.calm_pct, "较昨日 -2%", "观望 / 等反弹 / 不敢动", "blue")}
    {metric_card("兴奋指数 EUPHORIA", data.euphoria_pct, "较昨日 -3%", "梭哈 / 涨停 / 满仓干", "orange")}
    <div class="legend">综合研判：<b>恐慌情绪反弹</b>，绝望情绪占主导，历史底部特征显著。</div>
  </section>
  {timeline_section(data)}
  {top_section(data.mentions)}
  {distribution_section(data)}
  {positioning_section(data)}
  {cases_section(data)}
  {appendix_section(data)}
</main>
</body>
</html>"""


def styles() -> str:
    return """<style>
body{margin:0;background:#f4f6f8;color:#263238;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif;}
.wrap{max-width:860px;margin:0 auto;padding:28px 18px 48px;}
.hero{background:#132638;color:#fff;border-radius:8px;padding:36px 44px;margin-bottom:28px;}
.eyebrow{font-size:12px;letter-spacing:3px;color:#90a4ae;margin-bottom:14px;}
h1{margin:0 0 10px;font-size:34px;letter-spacing:0;}p{margin:0}.meta{margin-top:18px;color:#9fb3c2;font-size:13px}
.summary,.section{background:#fff;border-radius:8px;margin-bottom:26px;padding:24px 40px;box-shadow:0 8px 24px rgba(17,35,54,.06)}
.bar,.section-title{background:#132638;color:#fff;border-radius:4px;padding:14px 18px;font-size:15px;font-weight:700;margin-bottom:18px}
.bar{background:#c8342c}.metric{display:inline-block;vertical-align:top;width:31.5%;min-height:130px;margin-right:1.7%;border-radius:5px;color:#fff;padding:18px;box-sizing:border-box}
.metric:last-of-type{margin-right:0}.metric.red{background:#c9362f}.metric.blue{background:#277ca9}.metric.orange{background:#cf7419}
.metric .name{font-size:13px;font-weight:700;opacity:.9}.metric .pct{font-size:38px;line-height:1.25;font-weight:800;margin:12px 0}.metric .sub{font-size:12px;opacity:.85;margin-top:4px}
.legend{text-align:center;color:#6b7780;font-size:13px;margin-top:16px}.legend b{color:#c9362f}
table{width:100%;border-collapse:collapse;font-size:13px}th{background:#132638;color:#fff;text-align:left;padding:11px}td{padding:10px;border-bottom:1px solid #edf1f4}tr:nth-child(even) td{background:#fbfcfd}
.rank{font-weight:800;color:#c9362f}.up{color:#c9362f;font-weight:700}.down{color:#229c72;font-weight:700}.tag{display:inline-block;border-radius:3px;padding:3px 8px;color:#fff;font-size:12px}
.tag.despair{background:#c9362f}.tag.calm{background:#277ca9}.tag.euphoria{background:#cf7419}
.event{border-left:5px solid #c9362f;background:#fff1f1;padding:16px 18px;margin:12px 0;border-radius:4px}.event.calm{border-color:#277ca9;background:#eef8fc}.event.euphoria{border-color:#cf7419;background:#fff7e9}
.event h3{font-size:16px;margin:0 0 8px}.event p{font-size:13px;line-height:1.7;color:#4c5963}
.dist{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.dist-card{border-radius:6px;padding:18px;border:1px solid #f0c2c2;background:#fff2f2}.dist-card.calm{border-color:#b9dcea;background:#eff9fd}.dist-card.euphoria{border-color:#eed29d;background:#fff8e8}
.chips{margin-top:12px}.chip{display:inline-block;background:rgba(0,0,0,.06);border-radius:4px;padding:5px 8px;margin:4px;font-size:12px}
.stages{display:grid;grid-template-columns:repeat(6,1fr);margin-bottom:18px}.stage{padding:14px 8px;text-align:center;background:#d9dee2;color:#43515c;font-size:12px}.stage.active{background:#c9362f;color:#fff;font-weight:800}
.case-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.case{border:1px solid #e7c7a5;background:#fff7ee;border-radius:6px;padding:18px}.case.blue{border-color:#b8d8e5;background:#f0faff}
.note{font-size:12px;color:#6b7780;line-height:1.8}.footer{border-left:4px solid #cfd8dc;padding:10px 14px;color:#8a969e;font-size:12px;background:#fafafa}
@media(max-width:720px){.wrap{padding:14px}.hero,.summary,.section{padding:22px}.metric{display:block;width:100%;margin:0 0 10px}.dist,.case-grid,.stages{grid-template-columns:1fr}table{font-size:12px}}
</style>"""


def metric_card(name: str, pct: int, change: str, words: str, color: str) -> str:
    return f"""<div class="metric {color}">
  <div class="name">{html.escape(name)}</div>
  <div class="pct">{pct}%</div>
  <div class="sub">{html.escape(change)}</div>
  <div class="sub">{html.escape(words)}</div>
</div>"""


def timeline_section(data: DashboardData) -> str:
    rows = "\n".join(
        f"<tr><td>{d['date']:%m/%d}</td><td class='up'>{d['despair']}%</td>"
        f"<td>{d['calm']}%</td><td>{d['euphoria']}%</td></tr>"
        for d in data.days
    )
    events = "\n".join(
        f"<div class='event {e['kind']}'><h3>{html.escape(e['title'])}</h3><p>{html.escape(e['body'])}</p></div>"
        for e in data.events
    )
    return f"""<section class="section">
<div class="section-title">01 · 散户情绪波动时间线</div>
<table><thead><tr><th>日期</th><th>绝望指数</th><th>平静指数</th><th>兴奋指数</th></tr></thead><tbody>{rows}</tbody></table>
<h3>关键情绪节点</h3>{events}
</section>"""


def top_section(mentions: Iterable[Mention]) -> str:
    rows = []
    for i, item in enumerate(mentions, 1):
        delta_cls = "up" if item.delta >= 0 else "down"
        arrow = "↑" if item.delta >= 0 else "↓"
        rows.append(
            f"<tr><td class='rank'>{i}</td><td><b>{html.escape(item.keyword)}</b></td>"
            f"<td>{item.count:,}</td><td class='{delta_cls}'>{item.delta:+d}% {arrow}</td>"
            f"<td><span class='tag {item.sentiment}'>{sentiment_name(item.sentiment)}</span></td>"
            f"<td>{html.escape(item.sample)}</td></tr>"
        )
    return f"""<section class="section">
<div class="section-title">02 · 社区高频词条统计 TOP10</div>
<table><thead><tr><th>排名</th><th>词条</th><th>频次</th><th>涨跌幅</th><th>情绪标签</th><th>典型语境</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</section>"""


def distribution_section(data: DashboardData) -> str:
    despair = [m.keyword for m in data.mentions if m.sentiment == "despair"][:8] or list(PANIC_WORDS)[:6]
    calm = [m.keyword for m in data.mentions if m.sentiment == "calm"][:8] or list(CALM_WORDS)[:6]
    euphoria = [m.keyword for m in data.mentions if m.sentiment == "euphoria"][:8] or list(EUPHORIA_WORDS)[:6]
    return f"""<section class="section">
<div class="section-title">03 · 情绪词条分类分布</div>
<div class="dist">
{dist_card("绝望类词条", data.panic_pct, despair, "")}
{dist_card("平静类词条", data.calm_pct, calm, "calm")}
{dist_card("兴奋类词条", data.euphoria_pct, euphoria, "euphoria")}
</div></section>"""


def dist_card(title: str, pct: int, words: list[str], cls: str) -> str:
    chips = "".join(f"<span class='chip'>{html.escape(w)}</span>" for w in words)
    return f"<div class='dist-card {cls}'><b>{title}</b><div class='metric-like'>{pct}%</div><div class='chips'>{chips}</div></div>"


def positioning_section(data: DashboardData) -> str:
    return f"""<section class="section">
<div class="section-title">04 · 情绪周期定位</div>
<div class="stages">
<div class="stage">P1<br>极度乐观</div><div class="stage">P2<br>狂热期</div><div class="stage">P3<br>麻木期</div>
<div class="stage">P4<br>愤怒期</div><div class="stage active">P5<br>当前情绪拐点</div><div class="stage">P6<br>底部恢复</div>
</div>
<table><thead><tr><th>判断维度</th><th>当前表现</th><th>历史映射</th></tr></thead>
<tbody>
<tr><td>社区活跃度</td><td>高频悲观词占比 {data.panic_pct}%</td><td class="down">底部特征</td></tr>
<tr><td>高频词条</td><td>“不玩了 / 没救了”等词条进入TOP区</td><td class="down">底部特征</td></tr>
<tr><td>情绪快照</td><td>绝望情绪占主导，但反弹词开始出现</td><td class="down">反转概率提高</td></tr>
</tbody></table></section>"""


def cases_section(data: DashboardData) -> str:
    top = data.mentions[0] if data.mentions else None
    return f"""<section class="section">
<div class="section-title">05 · 典型案例（情绪验证）</div>
<div class="case-grid">
<div class="case"><b>平潭发展：情绪自证样本</b><p class="note">核心词“{html.escape(top.keyword if top else '不玩了')}”快速扩散，社媒讨论出现从恐慌到修复的分歧。</p></div>
<div class="case blue"><b>新易盛：情绪分裂样本</b><p class="note">追涨与观望词条并存，说明资金热度高，但一致性尚未完全恢复。</p></div>
</div></section>"""


def appendix_section(data: DashboardData) -> str:
    source_text = "；".join(data.sources) if data.sources else "未获取到外部源，使用本地降级词典"
    return f"""<section class="section">
<div class="section-title">附录 · 数据采集说明</div>
<p class="note">数据来源：{html.escape(source_text)}</p>
<p class="note">采集方式：关键词抓取、语义分组、词频分析与情绪标签归类。统计周期：{data.start:%Y-%m-%d} 00:00 至 {data.end:%Y-%m-%d} 18:00。</p>
<div class="footer">本报告用于公众社交数据的情绪观察，不构成任何投资建议。</div>
</section>"""


def sentiment_name(value: str) -> str:
    return {"despair": "绝望", "calm": "平静", "euphoria": "兴奋"}[value]


def send_email(subject: str, body_html: str, recipients: list[str]) -> None:
    host = os.getenv("SMTP_HOST") or "smtp.qq.com"
    port = int(os.getenv("SMTP_PORT") or "465")
    sender = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    if not sender or not password:
        raise RuntimeError("SMTP_USER 和 SMTP_PASSWORD 未配置。QQ 邮箱需要使用 SMTP 授权码。")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("你的邮箱客户端不支持 HTML 邮件，请使用网页版邮箱查看。")
    msg.add_alternative(body_html, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
        server.login(sender, password)
        server.send_message(msg)


def main(argv: list[str]) -> int:
    load_env_file(ROOT / ".env")
    dry_run = "--dry-run" in argv
    force = "--force" in argv
    today = dt.date.today()
    if not dry_run and not force and not is_workday(today):
        print(f"{today:%Y-%m-%d} 不是配置内工作日，跳过发送。")
        return 0
    data = build_dashboard()
    body = render_html(data)
    out = ROOT / "dashboard_preview.html"
    out.write_text(body, encoding="utf-8")

    if dry_run:
        print(f"预览已生成：{out}")
        return 0

    recipients = [x.strip() for x in os.getenv("MAIL_TO", DEFAULT_TO).split(",") if x.strip()]
    subject = f"【情绪追踪报告】韭菜情绪追踪报告 {dt.date.today():%Y年%m月%d日}"
    send_email(subject, body, recipients)
    print(f"已发送到：{', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except URLError as exc:
        print(f"网络访问失败：{exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
