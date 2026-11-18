"""把一段「语音随口说的话」拆成三样东西：标签、介绍、感受，再排成一页手账。

    tag    店名 / 品牌 / 术语，比如 "basickin"
    intro  这是什么、卖什么、什么材料——客观说明
    feel   我的感受、情绪

没有网络时这是纯规则版；planner.plan() 是唯一的出入口，
以后想接大模型，只要让 LLM 返回同样的 Plan 结构即可。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from dataclasses import dataclass, field

MAX_CHARS = 16  # 单行最多字数（再长就折行）

FILLERS = (
    "然后", "就是", "那个", "其实", "就是说", "呃", "嗯", "额", "啊",
    "我觉得", "我感觉", "反正", "所以说", "所以说呢", "但是", "不过",
)

CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "food": ("吃", "饭", "菜", "面", "火锅", "奶茶", "咖啡", "蛋糕", "面包", "甜品", "早餐",
             "午餐", "晚餐", "好吃", "香", "汤", "粥", "烧烤", "炸鸡", "寿司", "零食", "水果", "菌"),
    "beauty": ("美甲", "化妆", "护肤", "口红", "发型", "头发", "妆", "香水", "面膜", "穿搭",
               "衣服", "裙子", "鞋", "包", "饰品", "美睫", "染发", "买", "逛"),
    "body": ("跑步", "健身", "瑜伽", "运动", "游泳", "爬山", "散步", "骑车", "帕梅拉", "拉伸", "打卡"),
    "work": ("工作", "加班", "会议", "项目", "汇报", "上班", "同事", "老板", "开会", "方案",
             "客户", "学习", "看书", "读书", "考试", "论文", "复盘"),
    "low": ("累", "疲惫", "烦", "焦虑", "难过", "委屈", "压力", "失眠", "熬夜", "心累", "崩溃", "摆烂"),
    "high": ("开心", "快乐", "满足", "幸福", "惊喜", "期待", "喜欢", "治愈", "舒服", "松弛", "惬意", "感动"),
    "weather": ("下雨", "晴天", "阳光", "云", "风", "雪", "天气", "阴天", "台风", "太阳"),
    "home": ("收拾", "家务", "打扫", "做饭", "猫", "狗", "宠物", "花", "植物", "家", "房间"),
    "travel": ("旅行", "出门", "公园", "海边", "城市", "街", "咖啡店", "展览", "博物馆",
               "电影", "音乐", "约", "见", "店"),
    "night": ("深夜", "凌晨", "睡前", "夜里", "晚安"),
}

# 感受类：出现这些词就当成「我的感受」
FEEL_WORDS = (
    "开心", "快乐", "满足", "幸福", "惊喜", "期待", "喜欢", "治愈", "舒服", "松弛",
    "惬意", "感动", "惊艳", "好看", "温暖", "放松", "累", "疲惫", "一般", "失望",
    "可惜", "踩雷", "值得", "好闻", "好吃", "好喝", "香", "甜", "心情", "氛围",
    "绝了", "chill", "好逛", "有趣", "可爱", "高级", "喜欢", "舒服", "安静", "热闹",
    "贵", "便宜", "划算", "惊喜", "普通", "谁懂", "走不动", "心动", "好像都要",
    "想要", "太好", "超", "不得了", "受不了", "种草", "剁手", "绝", "爱了", "救命",
)

# 介绍类：出现这些词就当成「客观说明」
INTRO_MARKERS = (
    "叫", "名字", "是卖", "是一家", "位于", "主打", "材料", "装修", "风格", "菜单",
    "价格", "开的", "成立", "主要是", "卖", "做的", "用的是", "来自", "号", "楼",
    "店", "品牌", "系列", "款",
)

PLACE_NOUN = {
    "beauty": "衣服店", "food": "餐厅", "travel": "小店", "home": "好物店",
    "work": "工位", "body": "运动场", "night": "夜景", "weather": "天气",
}

MOOD_ADJ = {"high": "好逛的", "low": "安静的小", "neutral": "舒服的"}

NOUN_HINT = (
    ("咖啡", "咖啡店"), ("衣服", "衣服店"), ("面包", "面包店"), ("书店", "书店"),
    ("花店", "花店"), ("酒吧", "酒吧"), ("餐厅", "餐厅"), ("面馆", "面馆"),
    ("展览", "展览"), ("公园", "公园"), ("酒店", "酒店"), ("商场", "商场"),
)

# 语音里常出现的品牌/店名形态
NAME_RE = re.compile(r"(?:叫|名字叫|名字是|店名是|品牌是|店叫)\s*([\u4e00-\u9fa5A-Za-z0-9&.\- ]{2,18})")
BRAND_RE = re.compile(r"(?<![A-Za-z])([A-Za-z%][A-Za-z0-9&.\-]{2,18})(?![A-Za-z])")
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "vlog", "ootd", "ok", "okay",
    "coffee", "cafe", "shop", "store", "new", "old", "so", "very",
}

# 关键词 → emoji，按出现顺序取前两个
EMOJI_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("咖啡", "拿铁", "美式"), "☕️"),
    (("奶茶",), "🧋"),
    (("蛋糕", "甜品", "下午茶"), "🍰"),
    (("面包", "可颂", "烘焙"), "🥐"),
    (("火锅",), "🍲"),
    (("面", "拉面", "意面"), "🍜"),
    (("饭", "拌饭", "盖饭"), "🍚"),
    (("酒", "鸡尾"), "🍷"),
    (("衣服", "穿搭", "裙子", "衬衫", "秋装"), "👗"),
    (("鞋", "球鞋"), "👟"),
    (("包", "包包"), "👜"),
    (("美甲",), "💅"),
    (("化妆", "口红", "妆"), "💄"),
    (("猫",), "🐱"),
    (("狗", "狗子"), "🐶"),
    (("花", "花店"), "🌸"),
    (("公园", "树", "草地"), "🌳"),
    (("展览", "看展"), "🖼️"),
    (("电影",), "🎬"),
    (("音乐", "耳机"), "🎧"),
    (("雨",), "🌧️"),
    (("晴", "阳光", "太阳"), "🌞"),
    (("云", "阴天"), "☁️"),
    (("雪",), "❄️"),
    (("买", "逛", "购物"), "🛍️"),
    (("睡", "晚安", "半夜"), "🌙"),
    (("开心", "快乐", "惊喜", "喜欢"), "✨"),
    (("累", "疲惫", "加班"), "🌙"),
    (("好吃", "香", "好喝"), "😋"),
)


CATEGORY_EMOJI: dict[str, tuple[str, ...]] = {
    "food": ("🍚", "🍜", "☕️", "🍰", "🥗"),
    "beauty": ("💅", "🌸", "✨"),
    "body": ("🌿", "🫧", "🏃"),
    "work": ("📎", "🕯️", "📖"),
    "low": ("🌙", "🫧", "☁️"),
    "high": ("✨", "💛", "🌞"),
    "weather": ("☁️", "🌞", "🌧️"),
    "home": ("🪴", "🐱", "🍞"),
    "travel": ("🚶", "📷", "🌊"),
    "night": ("🌙", "✨", "🕯️"),
    "life": ("✨", "🌿", "☕️"),
}

CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    "food": ("今日份美食", "好好吃饭"),
    "beauty": ("变美日记", "今日穿搭"),
    "body": ("运动打卡",),
    "work": ("打工日常", "学习记录"),
    "low": ("给自己放个假",),
    "high": ("快乐收集",),
    "weather": ("今日天气",),
    "home": ("把日子过好",),
    "travel": ("出门走走",),
    "night": ("晚安日记",),
}

PALETTE_HINT: dict[str, str] = {
    "food": "cream", "beauty": "blush", "body": "sage", "work": "paper",
    "low": "dusk", "high": "butter", "weather": "sky", "home": "paper",
    "travel": "sky", "night": "ink",
}

WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass
class Plan:
    title: str | None
    lines: list[str]
    tags: list[str]
    emoji: str
    palette: str
    date: str
    weekday: str
    tag: str | None = None
    emojis: list[str] = field(default_factory=list)
    intro: list[str] = field(default_factory=list)
    feel: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    mood: str = "neutral"
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "tag": self.tag,
            "intro": self.intro,
            "feel": self.feel,
            "lines": self.lines,
            "tags": self.tags,
            "emoji": self.emoji,
            "emojis": self.emojis,
            "palette": self.palette,
            "date": self.date,
            "weekday": self.weekday,
            "categories": self.categories,
            "mood": self.mood,
        }


def _strip_fillers(s: str) -> str:
    changed = True
    while changed:
        changed = False
        for f in FILLERS:
            if s.startswith(f) and len(s) > len(f) + 1:
                s = s[len(f):].lstrip("，, 、")
                changed = True
    return s.strip()


def split_clauses(text: str) -> list[str]:
    text = re.sub(r"[#＃][^\s#＃]+", " ", text)
    text = text.replace("\n", "。").replace("…", "")
    parts = re.split(r"[。！？!?；;]+", text)
    out: list[str] = []
    for p in parts:
        for q in re.split(r"[，,、]+", p):
            q = _strip_fillers(q.strip(" \t　\"'“”‘’"))
            if q:
                out.append(q)
    return out


def _balance(chunk: str, max_chars: int) -> list[str]:
    n = max(1, -(-len(chunk) // max_chars))
    size = -(-len(chunk) // n)
    return [chunk[i: i + size] for i in range(0, len(chunk), size)]


def _merge_short(clauses: list[str], min_len: int = 4) -> list[str]:
    out: list[str] = []
    buf = ""
    for c in clauses:
        if buf:
            if len(buf) + 1 + len(c) <= MAX_CHARS:
                buf = buf + "，" + c
                continue
            out.append(buf)
            buf = ""
        if len(c) < min_len:
            buf = c
        else:
            out.append(c)
    if buf:
        out.append(buf)
    return out


def _detect(raw: str) -> tuple[list[str], str]:
    cats = [k for k, words in CATEGORY_WORDS.items() if any(w in raw for w in words)]
    mood = "neutral"
    if "low" in cats:
        mood = "low"
    elif "high" in cats:
        mood = "high"
    return cats, mood


def _extract_tag(raw: str, cats: list[str], mood: str) -> str | None:
    """店名/品牌/术语：先找「叫…」，再找英文单词，最后按感受兜底总结。"""
    m = NAME_RE.search(raw)
    if m:
        cand = m.group(1).strip(" 　,，。.的")
        if 2 <= len(cand) <= 18 and not re.match(r"^(什么|那个|这个|啥|一家|哪)", cand):
            return cand

    for w in BRAND_RE.findall(raw):
        if w.lower() in STOPWORDS or len(w) < 3:
            continue
        if w.islower() and len(w) < 4:
            continue
        return w

    # 「X 店」这种中文店名
    m2 = re.search(r"([\u4e00-\u9fa5]{2,6})(?:店|铺|馆|工作室)", raw)
    if m2:
        name = m2.group(1)
        if not any(x in name for x in ("一家", "这家", "那家", "什么", "了", "的", "个")):
            return name + "店"

    place_like = any(k in raw for k in ("店", "馆", "铺", "逛", "吃", "喝", "住", "展"))
    if not place_like:
        return None
    noun = next((n for k, n in NOUN_HINT if k in raw), PLACE_NOUN.get(cats[0], "小店"))
    if any(w in raw for w in ("酷", "复古", "潮")):
        adj = "复古的"
    elif mood == "high":
        adj = "好逛的"
    elif any(w in raw for w in ("安静", "清净", "清净")):
        adj = "安静的"
    else:
        adj = "舒服的"
    return adj + noun


def _classify(clause: str) -> str:
    """把一句话分成 feel 还是 intro。"""
    feel_hits = sum(1 for w in FEEL_WORDS if w in clause)
    intro_hits = sum(1 for w in INTRO_MARKERS if w in clause)
    if feel_hits and feel_hits >= intro_hits:
        return "feel"
    if intro_hits:
        return "intro"
    return "feel" if feel_hits else "intro"


def plan(
    text: str,
    *,
    title: str | None = None,
    palette: str | None = None,
    date: str | None = None,
    seed: int = 0,
    max_lines: int = 7,
) -> Plan:
    raw = (text or "").strip()
    hashtags = re.findall(r"[#＃]([^\s#＃]+)", raw)
    clauses = split_clauses(raw)
    if not clauses:
        clauses = ["今天也有好好生活"]

    h = int(hashlib.md5(raw.encode("utf-8")).hexdigest()[:8], 16)
    seed = seed or h

    cats, mood = _detect(raw)
    if not cats:
        cats = ["life"]

    tag = _extract_tag(raw, cats, mood)

    # 标题只在显式指定时才用：不再把第一句话硬抬成标题
    t = (title or "").strip() or None

    # 标签本身不进正文
    if tag:
        clauses = [c for c in clauses if tag not in c] or clauses

    merged = _merge_short(clauses)
    intro_raw: list[str] = []
    feel_raw: list[str] = []
    for c in merged:
        (feel_raw if _classify(c) == "feel" else intro_raw).append(c)

    if not intro_raw and not feel_raw:
        intro_raw = ["今天也值得记一笔"]
    # 一句感受都没听出来时，把最后一句当感受（人说话通常最后才抒情）
    if not feel_raw and len(intro_raw) >= 2:
        feel_raw = [intro_raw.pop()]
    if not intro_raw:
        intro_raw = [tag] if tag else []

    def _lines(chunks: list[str], limit: int) -> list[str]:
        out: list[str] = []
        for c in chunks:
            out.extend(_balance(c, MAX_CHARS))
        return out[:limit]

    intro = _lines(intro_raw, 5)
    feel = _lines(feel_raw, 3)

    # 太长就截断最后一行
    def _clip(ls: list[str], limit: int) -> list[str]:
        if len(ls) > limit:
            ls = ls[:limit]
            ls[-1] = ls[-1][: MAX_CHARS - 1] + "…"
        return ls

    intro = _clip(intro, 5)
    feel = _clip(feel, 3)
    lines = (intro + feel)[:max_lines]

    tags: list[str] = []
    for c in cats[:2]:
        tags.extend(CATEGORY_TAGS.get(c, ()))
    tags.extend(hashtags)
    if len(tags) < 2:
        tags.append("生活记录")
    seen: set[str] = set()
    tags = [x for x in tags if not (x in seen or seen.add(x))][:3]

    picked = [e for words, e in EMOJI_WORDS if any(w in raw for w in words)]
    if not picked:
        picked = [CATEGORY_EMOJI.get(cats[0], ("✨",))[seed % len(CATEGORY_EMOJI.get(cats[0], ("✨",)))]]
    emoji = picked[0]
    emojis = list(dict.fromkeys(picked))[:2]

    if palette in (None, "", "suggest"):
        palette = PALETTE_HINT.get(cats[0], "ink")

    today = _dt.date.today()
    d = date or today.strftime("%Y.%m.%d")
    wd = WEEKDAY[today.weekday()] if not date else ""

    return Plan(
        title=t,
        tag=tag,
        intro=intro,
        feel=feel,
        lines=lines,
        tags=tags,
        emoji=emoji,
        emojis=emojis,
        palette=palette or "ink",
        date=d,
        weekday=wd,
        categories=cats,
        mood=mood,
        raw=raw,
    )
