"""Which languages a source is written in, and how they are laid out.

The ONE home for that question (docs/multilingual-plan.md §1). Deterministic,
no LLM call, run once per source at the point every reader passes
(`source_preview.source_preview_payload`), so a crawl, a document, a Facebook
Page and a paste are all read by the same rules.

How it reads, in order:

1. **Units, not pages.** Every distinct line of a page's text is a unit
   (heading, paragraph or label), plus the header's nav labels once. A footer
   repeated on every page is counted once, not once per page.
2. **Exclusions before counting.** The brand (title segments the pages share),
   people's names (profile cards, a page's subject), emails, phones, URLs and
   address lines say nothing about the language the owner WRITES in — "Unique
   Kindies 创立于 2006 年" counts its Chinese, not its brand. A Facebook digest's
   labels and its machine facts (hours, address, follower count) are Facebook's
   words, not the owner's, and are skipped the same way.
3. **Script first** — exact and free. Han, Kana, Hangul, Tamil, Arabic, Thai,
   Devanagari, Hebrew and Cyrillic each name their language; Han is split
   Simplified/Traditional by a small table of characters that differ, and
   reported as plain `zh` when the text holds none of them — never assumed.
4. **Latin through py3langid**, restricted to `settings.language_detect_candidates`.
   Malay and Indonesian are one family to the model (it calls Malaysian copy
   either), reported as `ms` unless `detect_market` says Indonesia. A Latin
   unit too short to identify (< `MIN_LATIN_LETTERS`) inherits its nearest
   identified neighbour on the page rather than being guessed.
5. **Coverage, not presence.** A language is *substantial* only past BOTH a
   share of the counted text and an absolute amount, so one Chinese phrase on
   an English site (Scenario F) is noted as incidental and changes nothing.
6. **Pattern**, by explainable rules, each with its evidence line:
   `parallel_urls` (translated mirrors at their own URLs) → `inline_bilingual`
   (units paired on one page: "Programmes | 课程", or adjacent units of
   comparable length) → `mixed_sections` (two substantial languages, unpaired)
   → `incidental` → `monolingual`.

The declared `<html lang>` is recorded as evidence and used only to break a tie
the text cannot (a Han text with no differential character): both mmta.org.my
and uniquekindies.com declare `lang="en"` on every language.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from app.config import settings
from app.models.content_blocks import (
    LanguagePair,
    LanguageProfile,
    LanguageShare,
    NavLink,
    PageLanguage,
    SourceContent,
)
from app.models.facebook import FactLabel
from app.services.contact_text import EMAIL_RE, phones_in
from app.services.locale import (
    LOCALES,
    canonical_locale,
    detect_market,
    locale_label,
    locale_segment,
)
from app.services.source_path import normalize_source_slug

# --- calibration -------------------------------------------------------------------
#
# Stated in docs/multilingual-plan.md "Progress and corrections" with the numbers
# they were set from. Change them there and here together.

# A Han/Kana/Hangul character carries roughly a word; a Latin letter a fifth of
# one. Counting raw characters would make a Chinese paragraph look a third the
# size of its English translation. See the plan's "Progress and corrections"
# for what this was set from.
CJK_WEIGHT = 2.5
# A language is substantial only past BOTH: a share of the counted text...
MIN_SHARE = 0.15
# ...and an absolute amount, in weighted letters (~2 sentences of English,
# ~65 Chinese characters).
MIN_AMOUNT = 240
# Shorter Latin text is not identified (py3langid calls "Programmes" German);
# it inherits a neighbour's language instead.
MIN_LATIN_LETTERS = 12
# Below this py3langid probability a Latin reading is a guess, and the unit
# inherits a neighbour instead. Set from the fixtures: every misreading scored
# 0.21-0.26 ("Our Programmes" → de), every correct paragraph 0.88+.
LATIN_MIN_CONFIDENCE = 0.5
# A paragraph is a unit with at least this many weighted letters; shorter
# non-heading units are labels (buttons, captions, list items).
PARAGRAPH_LETTERS = 60
# Two adjacent units in different languages pair when their weighted sizes are
# within this ratio — a translation is about as long as its original.
PAIR_LENGTH_RATIO = 2.5
# Pairs on one site before the layout reads as deliberately bilingual.
MIN_PAIRS = 2
# Pairs kept on the profile (the preview shows a few; P2 will re-derive them).
MAX_PAIRS_KEPT = 40

# --- scripts ------------------------------------------------------------------------

_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0400, 0x04FF, "Cyrl"),
    (0x0590, 0x05FF, "Hebr"),
    (0x0600, 0x06FF, "Arab"),
    (0x0750, 0x077F, "Arab"),
    (0x0900, 0x097F, "Deva"),
    (0x0B80, 0x0BFF, "Taml"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x1100, 0x11FF, "Hang"),
    (0x3040, 0x30FF, "Kana"),
    (0x3130, 0x318F, "Hang"),
    (0x31F0, 0x31FF, "Kana"),
    (0x3400, 0x4DBF, "Hani"),
    (0x4E00, 0x9FFF, "Hani"),
    (0xAC00, 0xD7AF, "Hang"),
    (0xF900, 0xFAFF, "Hani"),
    (0xFB50, 0xFDFF, "Arab"),
    (0xFE70, 0xFEFF, "Arab"),
    (0x20000, 0x2FFFF, "Hani"),
)

# A script that names its language outright. Han is resolved site-wide below
# (zh / zh-Hans / zh-Hant, or ja when Kana rides along); Latin goes to py3langid.
# Arabic script is reported as `ar` and Cyrillic as `ru` — the candidates this
# product has met; Urdu, Persian, Ukrainian would need a model of their own.
_SCRIPT_LANGUAGE = {
    "Cyrl": "ru",
    "Hebr": "he",
    "Arab": "ar",
    "Deva": "hi",
    "Taml": "ta",
    "Thai": "th",
    "Hang": "ko",
    "Kana": "ja",
}
_WIDE_SCRIPTS = frozenset({"Hani", "Kana", "Hang"})


def _script_of(char: str) -> str | None:
    code = ord(char)
    if code < 0x0250 or 0x1E00 <= code <= 0x1EFF:  # Latin, incl. Vietnamese
        return "Latn" if char.isalpha() else None
    for low, high, script in _SCRIPT_RANGES:
        if low <= code <= high:
            return script
    return None


# --- Simplified vs Traditional --------------------------------------------------------
#
# Common characters whose two forms differ, as (Simplified, Traditional) pairs.
# Characters that are ALSO ordinary in the other script (后 皇后, 台 台灣, 系, 准,
# 面, 干, 只, 余, 于, 云, 志, 范, 里, 叶, 才, 制, 松, 户) are left out on purpose: a
# hit must say which script it is.
_SIMPLIFIED_TRADITIONAL = (
    "这這 们們 个個 来來 时時 国國 学學 会會 为為 说說 对對 发發 过過 还還 没沒 样樣 "
    "经經 现現 动動 种種 开開 关關 长長 问問 间間 门門 见見 东東 车車 书書 语語 话話 "
    "课課 师師 园園 儿兒 爱愛 让讓 请請 谢謝 认認 识識 读讀 写寫 听聽 买買 卖賣 钱錢 "
    "银銀 员員 实實 业業 与與 两兩 从從 进進 运運 头頭 点點 电電 网網 页頁 题題 体體 "
    "机機 礼禮 节節 华華 历歷 观觀 报報 务務 区區 处處 号號 联聯 络絡 线線 质質 价價 "
    "费費 优優 势勢 产產 设設 计計 划劃 维維 护護 环環 视視 频頻 广廣 场場 馆館 医醫 "
    "疗療 药藥 际際 级級 组組 织織 积積 极極 乐樂 欢歡 戏戲 剧劇 艺藝 术術 画畫 图圖 "
    "习習 练練 选選 择擇 标標 责責 负負 贵貴 预預 约約 岁歲 龄齡 妈媽 爷爺 宝寶 贝貝 "
    "亲親 营營 养養 饭飯 饮飲 鸡雞 鱼魚 汤湯 庆慶 纪紀 团團 队隊 领領 导導 协協 议議 "
    "讯訊 达達 迈邁 尔爾 马馬 侨僑 汉漢 简簡 证證 资資 询詢 访訪 应應 该該 将將 当當 "
    "带帶 帮幫 难難 双雙 单單 总總 统統 结結 构構 数數 据據 详詳 细細 类類 别別 么麼 "
    "吗嗎 无無 气氣 热熱 风風 阳陽 钟鐘 万萬 亿億 闻聞 杂雜 测測 试試 验驗 众眾 伤傷 "
    "惯慣 规規 则則 状狀 况況 态態 帐帳 码碼 邮郵 楼樓 层層 厅廳 迁遷 转轉 换換 办辦 "
    "举舉 传傳 农農 讲講 坚堅 强強 绍紹 创創"
).split()
SIMPLIFIED_ONLY = frozenset(pair[0] for pair in _SIMPLIFIED_TRADITIONAL if pair[0] != pair[1])
TRADITIONAL_ONLY = frozenset(pair[1] for pair in _SIMPLIFIED_TRADITIONAL if pair[0] != pair[1])

# --- exclusions -------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# A street address: a postcode-sized number standing alone plus a comma-listed
# locality. Shape, not street vocabulary — a "Jalan"/"Road" list would be a
# silent off switch everywhere it wasn't written for.
_POSTCODE_RE = re.compile(r"(?<!\d)\d{4,6}(?!\d)")
_TITLE_SPLIT_RE = re.compile(r"\s+[|\-–—:·]\s+|\s*[|｜·]\s*")
# A heading that states one thing twice: "Programmes | 课程", "About / 关于我们".
_PAIR_SEPARATOR_RE = re.compile(r"\s*[|｜/／·]\s*|\s+[-–—]\s+")

# Facebook digest labels whose values are machine facts in Facebook's own
# vocabulary (day names, "Followers on Facebook"), not the owner's prose.
_FACT_LABELS_SKIPPED = frozenset({
    FactLabel.CATEGORIES, FactLabel.SERVICE_OPTIONS, FactLabel.FOUNDED,
    FactLabel.PRICE_RANGE, FactLabel.ADDRESS, FactLabel.SERVICE_AREA,
    FactLabel.PHONE, FactLabel.EMAIL, FactLabel.WEBSITE, FactLabel.ALSO_ON,
    FactLabel.HOURS, FactLabel.FOLLOWERS, FactLabel.RATING,
})
_FACT_LABEL_RE = re.compile(
    r"^(" + "|".join(re.escape(str(label)) for label in FactLabel) + r"):\s*(.*)$"
)


@dataclass
class _Unit:
    page: int
    kind: str  # "heading" | "paragraph" | "label" | "nav"
    text: str  # after exclusions
    letters: Counter = field(default_factory=Counter)  # script → raw letter count
    latin_text: str = ""
    # Resolved: language → weighted amount, and Latin identification confidence.
    amounts: dict[str, float] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    latin_lang: str | None = None
    latin_conf: float = 0.0
    latin_inherited: bool = False

    @property
    def weight(self) -> float:
        return sum(self.amounts.values())

    @property
    def language(self) -> str | None:
        if not self.amounts:
            return None
        return max(self.amounts.items(), key=lambda kv: kv[1])[0]


# --- py3langid ---------------------------------------------------------------------------


def _candidates() -> tuple[str, ...]:
    return tuple(
        code.strip() for code in settings.language_detect_candidates.split(",") if code.strip()
    )


@lru_cache(maxsize=4)
def _identifier(candidates: tuple[str, ...]):
    """The py3langid model restricted to `candidates` (a ~2 MB load, once)."""
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
    identifier.set_languages(list(candidates))
    return identifier


def _identify_latin(text: str) -> tuple[str, float]:
    """(language, confidence) for a Latin run; Malay+Indonesian as one family."""
    ranked = _identifier(_candidates()).rank(text)
    family: dict[str, float] = defaultdict(float)
    for lang, prob in ranked:
        family["ms" if lang in ("ms", "id") else lang] += float(prob)
    lang, prob = max(family.items(), key=lambda kv: kv[1])
    return lang, prob


# --- reading units -------------------------------------------------------------------------


def _pages(source: SourceContent) -> list[SourceContent]:
    return [source, *source.discovered_pages]


def _path_of(page: SourceContent) -> str:
    return page.url_path or "/"


def _brand_strings(source: SourceContent) -> list[str]:
    """Title segments most pages share — the site's name, in whatever language.

    A one-page source has nothing to compare, so its brand is not guessed: the
    script/coverage rules keep a brand's few characters incidental anyway.
    """
    pages = _pages(source)
    titled = [p.title for p in pages if p.title]
    if len(titled) < 2:
        return []
    counts: Counter = Counter()
    for title in titled:
        counts.update({seg.strip() for seg in _TITLE_SPLIT_RE.split(title) if seg.strip()})
    floor = max(2, len(titled) // 2)
    return [seg for seg, n in counts.items() if n >= floor]


def _person_names(source: SourceContent) -> list[str]:
    names: set[str] = set()
    for page in _pages(source):
        if page.subject_name:
            names.add(page.subject_name)
        for profile in page.profile_candidates:
            if profile.name:
                names.add(profile.name)
    return sorted(names, key=len, reverse=True)


def _strip_exclusions(text: str, excluded: list[str]) -> str:
    text = _URL_RE.sub(" ", text)
    text = EMAIL_RE.sub(" ", text)
    for phone in phones_in(text):
        text = text.replace(phone, " ")
    for value in excluded:
        if value:
            text = re.sub(re.escape(value), " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _is_address(line: str) -> bool:
    return line.count(",") >= 2 and _POSTCODE_RE.search(line) is not None


def _digest_lines(raw_text: str) -> list[str]:
    """A Facebook fact digest, reduced to the owner's own words.

    The first line is the Page's name and Facebook category; labels are
    Facebook's; skipped facts take their indented continuation with them.
    """
    kept: list[str] = []
    skipping = False
    for index, line in enumerate(raw_text.splitlines()):
        if index == 0:
            continue
        match = _FACT_LABEL_RE.match(line)
        if match:
            skipping = match.group(1) in {str(label) for label in _FACT_LABELS_SKIPPED}
            if not skipping and match.group(2).strip():
                kept.append(match.group(2))
            continue
        if line.startswith((" ", "\t")):
            if not skipping:
                kept.append(line)
            continue
        skipping = False
        kept.append(line)
    return kept


def _nav_labels(links: list[NavLink]) -> list[str]:
    labels: list[str] = []
    for link in links:
        labels.append(link.label)
        labels.extend(_nav_labels(link.children))
    return labels


def _measure(unit: _Unit) -> None:
    latin: list[str] = []
    for char in unit.text:
        script = _script_of(char)
        if script is None:
            if latin and latin[-1] != " ":
                latin.append(" ")
            continue
        unit.letters[script] += 1
        latin.append(char if script == "Latn" else " ")
    unit.latin_text = re.sub(r"\s+", " ", "".join(latin)).strip()


def _read_units(source: SourceContent, brand_name: str | None = None) -> list[_Unit]:
    excluded = [*([brand_name] if brand_name else []), *_brand_strings(source), *_person_names(source)]
    excluded.sort(key=len, reverse=True)  # "Golden Lotus 金莲" before "Golden Lotus"
    seen: set[str] = set()
    units: list[_Unit] = []

    def add(page_index: int, kind: str, raw: str) -> None:
        line = raw.strip()
        if not line or _is_address(line):
            return
        text = _strip_exclusions(line, excluded)
        key = text.casefold()
        if not text or key in seen:
            return
        seen.add(key)
        unit = _Unit(page=page_index, kind=kind, text=text)
        _measure(unit)
        if not unit.letters:
            return
        if kind != "heading" and kind != "nav":
            wide = sum(n for s, n in unit.letters.items() if s in _WIDE_SCRIPTS)
            size = wide * CJK_WEIGHT + (sum(unit.letters.values()) - wide)
            unit.kind = "paragraph" if size >= PARAGRAPH_LETTERS else "label"
        units.append(unit)

    for page_index, page in enumerate(_pages(source)):
        headings = {h.strip().casefold() for h in page.headings if h.strip()}
        lines = (
            _digest_lines(page.raw_text)
            if source.source_kind == "facebook"
            else page.raw_text.splitlines()
        )
        for line in lines:
            add(page_index, "heading" if line.strip().casefold() in headings else "body", line)
        # Headings the text spine left out still count, once.
        for heading in page.headings:
            add(page_index, "heading", heading)
        if page_index == 0:
            for label in _nav_labels(page.nav_links):
                add(page_index, "nav", label)
    return units


# --- identifying -------------------------------------------------------------------------


def _resolve_latin(units: list[_Unit]) -> None:
    """Identify each Latin run; a short one inherits its nearest identified
    neighbour on the same page (a site-wide majority as the last resort)."""
    guesses: dict[int, tuple[str, float]] = {}
    for index, unit in enumerate(units):
        latin = unit.letters.get("Latn", 0)
        if latin >= MIN_LATIN_LETTERS:
            lang, conf = _identify_latin(unit.latin_text)
            if conf >= LATIN_MIN_CONFIDENCE:
                unit.latin_lang, unit.latin_conf = lang, conf
            else:
                guesses[index] = (lang, conf)

    identified = [u for u in units if u.latin_lang]
    majority = Counter(u.latin_lang for u in identified).most_common(1)
    fallback = majority[0][0] if majority else None

    for index, unit in enumerate(units):
        latin = unit.letters.get("Latn", 0)
        if not latin or unit.latin_lang:
            continue
        other = sum(n for s, n in unit.letters.items() if s != "Latn")
        if other and other * (CJK_WEIGHT if unit.letters.keys() & _WIDE_SCRIPTS else 1) > latin:
            continue  # a loanword or a number's unit inside another language's line
        neighbour = _nearest_identified(units, index)
        if neighbour is not None:
            unit.latin_lang, unit.latin_conf = neighbour.latin_lang, neighbour.latin_conf * 0.5
        elif fallback:
            unit.latin_lang, unit.latin_conf = fallback, 0.25
        elif index in guesses:  # nothing better to go on: keep the weak reading
            unit.latin_lang, unit.latin_conf = guesses[index]
        unit.latin_inherited = True


def _nearest_identified(units: list[_Unit], index: int) -> _Unit | None:
    """The closest unit on the same page whose Latin text was identified, the
    preceding one winning a tie (a heading's language is its section's)."""
    page = units[index].page
    candidates = [
        (abs(i - index), i > index, unit) for i, unit in enumerate(units)
        if i != index and unit.page == page and unit.latin_lang and not unit.latin_inherited
    ]
    return min(candidates, key=lambda c: (c[0], c[1]))[2] if candidates else None


def _han_variant(units: list[_Unit], declared: list[str]) -> tuple[str, float, str]:
    """`zh-Hans` / `zh-Hant` / `zh` for the site's Han text, with confidence and evidence."""
    simplified = traditional = 0
    for unit in units:
        for char in unit.text:
            if char in SIMPLIFIED_ONLY:
                simplified += 1
            elif char in TRADITIONAL_ONLY:
                traditional += 1
    total = simplified + traditional
    if total:
        code = "zh-Hans" if simplified >= traditional else "zh-Hant"
        share = max(simplified, traditional) / total
        return code, round(share, 2), (
            f"Chinese is {'Simplified' if code == 'zh-Hans' else 'Traditional'}: "
            f"{simplified} Simplified-only vs {traditional} Traditional-only characters"
        )
    for claim in declared:
        canonical = canonical_locale(claim)
        if canonical in ("zh-Hans", "zh-Hant"):
            return canonical, 0.5, f"Chinese script taken from the declared lang=\"{claim}\""
    return "zh", 0.5, "Chinese script undetermined: no character that differs between Simplified and Traditional"


def _assign_amounts(units: list[_Unit], han: str, han_conf: float, indonesia: bool) -> None:
    """Each unit's weighted letters per language, and how sure each reading is:
    py3langid's probability for Latin, the differential-table share for Han,
    certainty for every other script."""
    for unit in units:
        amounts: dict[str, float] = defaultdict(float)
        has_kana = unit.letters.get("Kana", 0) > 0
        for script, count in unit.letters.items():
            if script == "Latn":
                if unit.latin_lang:
                    lang = "id" if unit.latin_lang == "ms" and indonesia else unit.latin_lang
                    lang = canonical_locale(lang) or lang
                    amounts[lang] += count
                    unit.confidence[lang] = unit.latin_conf
                continue
            weight = CJK_WEIGHT if script in _WIDE_SCRIPTS else 1.0
            lang = han if script == "Hani" and not has_kana else _SCRIPT_LANGUAGE.get(script, "ja")
            amounts[lang] += count * weight
            unit.confidence.setdefault(lang, han_conf if lang == han else 1.0)
        unit.amounts = dict(amounts)


# --- pairs ---------------------------------------------------------------------------------


def _side_language(text: str, units: list[_Unit], index: int, han: str, indonesia: bool) -> str | None:
    probe = _Unit(page=units[index].page, kind="label", text=text)
    _measure(probe)
    if not probe.letters:
        return None
    wide = {s: n for s, n in probe.letters.items() if s != "Latn"}
    if wide and sum(wide.values()) >= probe.letters.get("Latn", 0):
        script = max(wide.items(), key=lambda kv: kv[1])[0]
        return han if script == "Hani" else _SCRIPT_LANGUAGE.get(script)
    lang = None
    if probe.letters.get("Latn", 0) >= MIN_LATIN_LETTERS:
        guess, conf = _identify_latin(probe.latin_text)
        lang = guess if conf >= LATIN_MIN_CONFIDENCE else None
    if lang is None:
        neighbour = _nearest_identified(units, index)
        lang = neighbour.latin_lang if neighbour else None
    if lang == "ms" and indonesia:
        lang = "id"
    return canonical_locale(lang) if lang else None


def _find_pairs(
    units: list[_Unit], paths: list[str], han: str, indonesia: bool
) -> list[LanguagePair]:
    pairs: list[LanguagePair] = []
    used: set[int] = set()
    for index, unit in enumerate(units):
        if unit.kind not in ("heading", "label", "nav"):
            continue
        sides = [s for s in _PAIR_SEPARATOR_RE.split(unit.text) if s.strip()]
        if len(sides) != 2:
            continue
        first = _side_language(sides[0], units, index, han, indonesia)
        second = _side_language(sides[1], units, index, han, indonesia)
        if first and second and first != second:
            used.add(index)
            pairs.append(LanguagePair(
                page=paths[unit.page], kind="label" if unit.kind == "nav" else unit.kind,
                first=sides[0].strip(), first_lang=first,
                second=sides[1].strip(), second_lang=second,
            ))

    for index in range(len(units) - 1):
        a, b = units[index], units[index + 1]
        if index in used or index + 1 in used or a.page != b.page:
            continue
        if a.kind != b.kind or a.kind == "nav":
            continue
        la, lb = a.language, b.language
        if not la or not lb or la == lb:
            continue
        # Each must be essentially one language, and the two about the same size.
        if a.amounts[la] < 0.8 * a.weight or b.amounts[lb] < 0.8 * b.weight:
            continue
        ratio = a.weight / b.weight if b.weight else 0
        if not (1 / PAIR_LENGTH_RATIO <= ratio <= PAIR_LENGTH_RATIO):
            continue
        used.update((index, index + 1))
        pairs.append(LanguagePair(
            page=paths[a.page], kind=a.kind, first=a.text, first_lang=la,
            second=b.text, second_lang=lb,
        ))
    return pairs


# --- the profile ---------------------------------------------------------------------------


def _url_locales(source: SourceContent) -> dict[int, str]:
    """Page index → the language its URL directory names, where the crawl proves it.

    Same evidence rule as `page_inference.translation_pairing` (a mirror counts
    when its untranslated counterpart was crawled too), asked of the same slugs.
    """
    from app.services.page_inference import translation_pairing

    slugs = {normalize_source_slug(p.url_path): i for i, p in enumerate(_pages(source))}
    out: dict[int, str] = {}
    for slug, (segment, _counterpart) in translation_pairing(set(slugs)).items():
        canonical = canonical_locale(segment)
        if canonical:
            out[slugs[slug]] = canonical
    return out


def _same_language(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a == b or (a.startswith("zh") and b.startswith("zh"))


def _name(code: str) -> str:
    return locale_label(code) if code in LOCALES else code


def analyze(source: SourceContent, *, brand_name: str | None = None) -> LanguageProfile:
    """The source's `LanguageProfile`. Pure: reads the source, never writes it.

    `brand_name` is the name the reader found for the business (og:site_name,
    a Facebook Page's name); it is excluded with the title segments the pages
    share, which is all a reader with no brand candidate can offer."""
    pages = _pages(source)
    paths = [_path_of(p) for p in pages]
    declared = [p.declared_lang for p in pages if p.declared_lang]
    units = _read_units(source, brand_name)
    evidence: list[str] = []

    if not units:
        return LanguageProfile(pattern="unknown", reason="No text to read a language from.")

    _resolve_latin(units)
    han, han_conf, han_note = _han_variant(units, declared)
    market = detect_market(
        "\n".join(p.raw_text for p in pages), [*source.links, source.source_ref]
    )
    indonesia = bool(market and market.country == "Indonesia")
    _assign_amounts(units, han, han_conf, indonesia)

    totals: dict[str, float] = defaultdict(float)
    conf_weight: dict[str, float] = defaultdict(float)
    per_page: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for unit in units:
        for lang, amount in unit.amounts.items():
            totals[lang] += amount
            per_page[unit.page][lang] += amount
            conf_weight[lang] += amount * unit.confidence.get(lang, 0.0)
    grand = sum(totals.values())
    if not grand:
        return LanguageProfile(pattern="unknown", reason="No text to read a language from.")

    url_locales = _url_locales(source)
    page_langs: list[PageLanguage] = []
    dominant: dict[int, str] = {}
    for index, page in enumerate(pages):
        counts = per_page.get(index)
        if counts:
            dominant[index] = max(counts.items(), key=lambda kv: kv[1])[0]
        page_langs.append(PageLanguage(
            path=paths[index],
            locale=dominant.get(index),
            url_locale=url_locales.get(index),
            declared=page.declared_lang,
        ))

    # Primary: the language of the pages at the root when mirrors exist (Q2's
    # "primary at the root" reads the source the same way), else the largest.
    root_counts: dict[str, float] = defaultdict(float)
    for index, lang in dominant.items():
        if index not in url_locales:
            root_counts[lang] += sum(per_page[index].values())
    primary = max((root_counts or totals).items(), key=lambda kv: kv[1])[0]

    def pages_carrying(lang: str) -> int:
        return sum(
            1 for counts in per_page.values()
            if counts.get(lang, 0) >= MIN_SHARE * sum(counts.values())
        )

    primary_pages = max(pages_carrying(primary), 1)
    shares: list[LanguageShare] = []
    for lang, amount in sorted(totals.items(), key=lambda kv: -kv[1]):
        share = amount / grand
        shares.append(LanguageShare(
            code=lang,
            label=_name(lang),
            share=round(share, 3),
            confidence=round(conf_weight[lang] / amount, 2) if amount else 0.0,
            coverage=round(min(1.0, pages_carrying(lang) / primary_pages), 2),
            substantial=share >= MIN_SHARE and amount >= MIN_AMOUNT,
            amount=int(round(amount)),
        ))
    substantial = [s for s in shares if s.substantial]
    minor = [s for s in shares if not s.substantial]

    if any(code.startswith("zh") for code in totals):
        evidence.append(han_note)
    if declared:
        claims = Counter(declared)
        claim, n = claims.most_common(1)[0]
        detected = [s.code for s in substantial]
        agrees = len(detected) == 1 and _same_language(canonical_locale(claim), detected[0])
        evidence.append(
            f'{n} of {len(pages)} page(s) declare lang="{claim}" — '
            + ("consistent with the text" if agrees else "recorded, not trusted: the text says otherwise")
        )

    pairs = _find_pairs(units, paths, han, indonesia)

    # Mirrors: a URL directory whose pages are written in the language it names.
    mirror_langs = sorted({
        lang for index, lang in url_locales.items()
        if _same_language(lang, dominant.get(index)) and not _same_language(lang, primary)
    })
    untranslated = sorted({
        lang for index, lang in url_locales.items()
        if dominant.get(index) and not _same_language(lang, dominant.get(index))
    })
    if untranslated:
        evidence.append(
            "Mirror director" + ("ies" if len(untranslated) > 1 else "y")
            + " " + ", ".join(f"/{c}" for c in untranslated)
            + " hold text in another language — not counted as a translation"
        )

    names = lambda items: ", ".join(_name(s.code) for s in items)  # noqa: E731
    if mirror_langs and len(substantial) >= 2:
        pattern = "parallel_urls"
        mirrored = [s for s in shares if any(_same_language(s.code, m) for m in mirror_langs)]
        detail = ", ".join(f"{_name(s.code)} on {round(s.coverage * 100)}% of pages" for s in mirrored)
        reason = f"Separate pages per language: {_name(primary)} at the root, {detail}."
        evidence.append(
            "Translated pages under " + ", ".join(
                sorted({f"/{locale_segment(paths[i]) or ''}" for i in url_locales
                        if _same_language(url_locales[i], dominant.get(i))})
            )
        )
    elif len(substantial) >= 2 and len(pairs) >= MIN_PAIRS:
        pattern = "inline_bilingual"
        # The language the owner writes FIRST in its pairs leads the page
        # ("Programmes | 课程"), whichever happens to have more letters.
        leads = Counter(p.first_lang for p in pairs).most_common()
        if leads and (len(leads) == 1 or leads[0][1] > leads[1][1]):
            if leads[0][0] != primary:
                evidence.append(
                    f"{_name(leads[0][0])} leads: it comes first in "
                    f"{leads[0][1]} of {len(pairs)} pairs"
                )
            primary = leads[0][0]
        substantial.sort(key=lambda s: s.code != primary)
        headings = sum(1 for p in pairs if p.kind == "heading")
        reason = (
            f"{names(substantial)} on the same pages: {len(pairs)} passages written in both"
            + (f", {headings} of them headings" if headings else "") + "."
        )
    elif len(substantial) >= 2:
        pattern = "mixed_sections"
        reason = f"{names(substantial)} on the same pages, in different sections rather than side by side."
    elif minor and minor[0].amount > 0 and substantial:
        pattern = "incidental"
        top = minor[0]
        size = (
            f"{round(top.share * 100)}% of the text"
            if top.share < MIN_SHARE
            else f"only {top.amount} letters"
        )
        reason = (
            f"{_name(substantial[0].code)}; a little {_name(top.code)} "
            f"({size}) is under the threshold and ignored."
        )
    elif substantial:
        pattern = "monolingual"
        reason = f"{_name(substantial[0].code)} only."
    else:
        pattern = "monolingual"
        reason = f"Mostly {_name(primary)}, but too little text to be sure."

    if pairs:
        evidence.append(f"{len(pairs)} paired passage(s), e.g. “{pairs[0].first}” / “{pairs[0].second}”")
    for item in shares:
        evidence.append(
            f"{_name(item.code)}: {round(item.share * 100)}% of the text "
            f"({item.amount} weighted letters, confidence {item.confidence})"
            + ("" if item.substantial else " — below threshold")
        )
    if indonesia and "id" in totals:
        evidence.append("Malay-family text reported as Indonesian: the market evidence says Indonesia")

    return LanguageProfile(
        primary=primary,
        languages=shares,
        pattern=pattern,
        pairs=pairs[:MAX_PAIRS_KEPT],
        evidence=evidence,
        reason=reason,
        pages=page_langs,
    )


def with_language_profile(source: SourceContent, *, brand_name: str | None = None) -> SourceContent:
    """`source` carrying its profile — a copy; the reader's object is untouched."""
    return source.model_copy(
        update={"language_profile": analyze(source, brand_name=brand_name)}
    )
