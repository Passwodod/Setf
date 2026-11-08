"""Language-independent interest concepts.

Each concept lists aliases in many languages (including transliterated forms). Matching works in a
romanised "skeleton" space so Cyrillic, Latin transliteration and typos land on the same concept, and
a stance (interest / mention / negative) is derived from nearby wording. Anything the table does not
know is left to the LLM layer; nothing here invents meaning.
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.ai.language import normalize, tokens

CONCEPTS: dict[str, dict] = {
    "programming": {
        "category": "technology",
        "aliases": [
            "programming", "coding", "software development", "developer", "programmer", "code",
            "программирование", "программист", "кодинг", "разработка по", "писать код", "кодить",
            "код жазу", "бағдарламалау", "бағдарламашы", "программалоо", "программист", "код жазуу",
            "programlama", "yazılım", "programmierung", "programmieren", "programmation", "programmer en",
            "programación", "programar", "编程", "写代码", "برمجة", "البرمجة",
        ],
    },
    "business": {
        "category": "business",
        "aliases": [
            "business", "entrepreneurship", "startup", "entrepreneur",
            "бизнес", "предпринимательство", "стартап", "предприниматель",
            "бизнес", "кәсіпкерлік", "іскерлік", "стартап", "ишкерлик", "бизнес", "ишкер",
            "iş dünyası", "girişimcilik", "unternehmertum", "geschäft", "entreprise", "entrepreneuriat",
            "negocio", "emprendimiento", "创业", "商业", "أعمال", "ريادة الأعمال",
        ],
    },
    "investing": {
        "category": "finance",
        "aliases": [
            "investing", "investment", "stocks", "crypto trading",
            "инвестиции", "инвестирование", "акции", "инвестировать",
            "инвестиция", "инвестициялау", "акциялар", "инвестиция салуу", "жатырым",
            "yatırım", "borsa", "investieren", "investition", "aktien", "investissement", "bourse",
            "inversión", "inversiones", "投资", "股票", "استثمار", "الاستثمار",
        ],
    },
    "artificial_intelligence": {
        "category": "technology",
        "aliases": [
            "artificial intelligence", "machine learning", "ai", "neural networks",
            "искусственный интеллект", "машинное обучение", "нейросети", "ии",
            "жасанды интеллект", "машиналық оқыту", "жасалма интеллект", "жасалма акыл", "машина үйрөнүү",
            "yapay zeka", "makine öğrenmesi", "künstliche intelligenz", "maschinelles lernen",
            "intelligence artificielle", "inteligencia artificial", "人工智能", "机器学习",
            "الذكاء الاصطناعي", "التعلم الآلي",
        ],
    },
    "football": {
        "category": "sports",
        "aliases": [
            "football", "soccer", "футбол", "футбол", "futbol", "fußball", "fussball", "football",
            "fútbol", "足球", "كرة القدم",
        ],
    },
    "music": {
        "category": "music",
        "aliases": [
            "music", "музыка", "музыку", "музыка", "музыка тыңдау", "ән", "ыр", "музыка угуу", "müzik",
            "musik", "musique", "música", "音乐", "موسيقى",
        ],
    },
    "travel": {
        "category": "travel",
        "aliases": [
            "travel", "traveling", "travelling", "путешествия", "путешествовать", "саяхат", "саякат",
            "жолоо", "seyahat", "gezmek", "reisen", "voyage", "voyager", "viajar", "viajes", "旅行",
            "旅游", "سفر",
        ],
    },
}

POSITIVE = (
    "интерес", "люблю", "обожаю", "занимаюсь", "увлека", "нравится", "изучаю", "учу",
    "love", "into", "passionate", "interested", "studying", "learning", "enjoy", "working on",
    "қызық", "ұнайды", "сүйемін", "үйренемін", "кызык", "жакшы көрөм", "жакшы корем", "үйрөнүп",
    "ilgi", "seviyorum", "öğreniyorum", "liebe", "interessiere", "j'aime", "m'intéresse", "me encanta",
    "me interesa", "喜欢", "感兴趣", "学习", "أحب", "مهتم",
)
NEGATIVE = (
    "не люблю", "ненавижу", "бесит", "не интересуюсь", "не нравится", "надоел", "терпеть не могу",
    "hate", "dislike", "can't stand", "cant stand", "not into", "sick of", "not interested",
    "ұнамайды", "жек көрем", "қызықпаймын", "жаккан жок", "жек көрөм", "кызыкпайм", "жактырбайм",
    "nefret", "sevmiyorum", "hasse", "déteste", "odio", "no me gusta", "讨厌", "不喜欢", "أكره", "لا أحب",
)

_CYR = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяәғқңөұүһі",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
     "u", "f", "kh", "ts", "ch", "sh", "sh", "", "y", "", "e", "yu", "ya", "a", "gh", "q", "ng", "o", "u",
     "u", "h", "i"], strict=True))
_SKEL = [("zh", "j"), ("kh", "h"), ("gh", "g"), ("sh", "s"), ("ch", "c"), ("ts", "c"), ("q", "k"),
         ("yu", "u"), ("ya", "a"), ("y", "i"), ("w", "v"), ("x", "ks"), ("ß", "ss"), ("ü", "u"), ("ö", "o"),
         ("ı", "i"), ("ç", "c"), ("ş", "s"), ("ğ", "g")]
NON_SPACED = re.compile(r"[\u4e00-\u9fff\u0600-\u06ff]")


def skeleton(word: str) -> str:
    w = "".join(_CYR.get(c, c) for c in normalize(word))
    for a, b in _SKEL:
        w = w.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", w)  # collapse doubled letters ("programming" ~ "programing")


@dataclass
class ConceptMatch:
    concept: str
    category: str
    alias: str
    matched_text: str
    stance: str  # interest | mention | negative
    fuzzy: bool


def _token_matches(tok_skel: str, alias_skel: str) -> tuple[bool, bool]:
    if tok_skel == alias_skel:
        return True, False
    if len(alias_skel) >= 5:
        stem = alias_skel[: max(5, len(alias_skel) - 3)]
        if tok_skel.startswith(stem) and abs(len(tok_skel) - len(alias_skel)) <= 5:
            return True, False
        if len(tok_skel) >= 5 and SequenceMatcher(None, tok_skel, alias_skel).ratio() >= 0.88:
            return True, True
    return False, False


def _stance(before: list[str], after: list[str]) -> str:
    window = " ".join(before[-5:]) + " | " + " ".join(after[:3])
    if any(m in window for m in NEGATIVE):
        return "negative"
    if any(m in window for m in POSITIVE):
        return "interest"
    return "mention"


_ALIAS_INDEX: list[tuple[str, str, str, list[str]]] = []
for _slug, _c in CONCEPTS.items():
    for _a in dict.fromkeys(_c["aliases"]):
        _ALIAS_INDEX.append((_slug, _c["category"], _a, [skeleton(t) for t in tokens(_a)] or [skeleton(_a)]))


def match_concepts(text: str) -> list[ConceptMatch]:
    norm = normalize(text)
    toks = tokens(text)
    skels = [skeleton(t) for t in toks]
    found: dict[str, ConceptMatch] = {}
    for slug, cat, alias, a_skels in _ALIAS_INDEX:
        if NON_SPACED.search(alias):
            idx = norm.find(alias)
            if idx >= 0 and slug not in found:
                before = norm[:idx].split()
                after = norm[idx + len(alias):].split()
                found[slug] = ConceptMatch(slug, cat, alias, alias, _stance(before, after) if before or after else "mention", False)
            continue
        n = len(a_skels)
        short = n == 1 and len(a_skels[0]) <= 3  # very short aliases: exact raw token only
        _raw_aliases = tokens(alias)
        for i in range(len(skels) - n + 1):
            results = [
                (toks[i + j] == _raw_aliases[j], False) if short else _token_matches(skels[i + j], a_skels[j])
                for j in range(n)
            ]
            if all(ok for ok, _ in results):
                fuzzy = any(f for _, f in results)
                if slug not in found or (found[slug].fuzzy and not fuzzy):
                    found[slug] = ConceptMatch(slug, cat, alias, " ".join(toks[i:i + n]),
                                               _stance(toks[:i], toks[i + n:]), fuzzy)
                break
    # a negative stance anywhere wins over a bare mention for the same concept
    return list(found.values())


def search_terms(query: str) -> dict[str, list[str]]:
    """Expand a query into every alias (incl. transliterations) of the concepts it mentions, so a
    Russian query also matches English/Kazakh/Kyrgyz/translit text. Unknown words pass through."""
    matches = match_concepts(query)
    terms: dict[str, list[str]] = {}
    for m in matches:
        aliases = CONCEPTS[m.concept]["aliases"]
        extra = [" ".join(_romanise_plain(t) for t in tokens(a)) for a in aliases]
        terms[m.concept] = list(dict.fromkeys([*aliases, *extra]))
    return terms


def _romanise_plain(word: str) -> str:
    return "".join(_CYR.get(c, c) for c in normalize(word))
