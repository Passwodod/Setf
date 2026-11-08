"""Language detection and informal-language interpretation (deterministic layer).

This layer is a fast, explainable pre-processor and fallback. It never rewrites the source text and it
refuses to guess: an expression with several plausible meanings is reported as ambiguous unless the
surrounding context selects one. Deep semantic extraction is done by the LLM in the AI engine, which
receives these annotations as hints.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import get_close_matches

TOKEN_RE = re.compile(r"[\w'’]+", re.UNICODE)

KK_ONLY = set("әғқұіһ")
KK_KY_SHARED = set("үөң")
TR_CHARS = set("ğış")
DE_CHARS = set("ß")

STOPWORDS = {
    "en": {"the", "is", "are", "you", "what", "and", "to", "of", "it", "this", "that", "my", "going", "working", "from", "home", "let's", "lets", "do", "doing", "have", "i"},
    "de": {"der", "die", "das", "ist", "und", "nicht", "ich", "du", "wir", "mit", "ein", "eine", "heute", "bin"},
    "fr": {"le", "la", "les", "est", "et", "je", "tu", "nous", "pas", "une", "un", "avec", "aujourd'hui", "suis", "des"},
    "es": {"el", "la", "los", "es", "y", "yo", "tú", "no", "una", "un", "con", "hoy", "estoy", "que", "por"},
    "tr": {"bir", "ve", "bu", "ben", "sen", "değil", "çok", "bugün", "için", "ama", "ne", "yapıyorsun", "nasılsın"},
    # transliterated Russian / Kazakh / Kyrgyz written with Latin letters
    "ru-Latn": {"privet", "kak", "dela", "chto", "ya", "ty", "ne", "da", "net", "segodnya", "poydem", "davay", "krasavchik", "spasibo", "normalno", "norm"},
    "kk-Latn": {"salem", "qalaisyn", "qalaysyn", "jaqsy", "zhaksy", "rahmet", "bugin", "ertengi", "emes", "iya", "bolady", "boladi", "ote", "qosh"},
    "ky-Latn": {"salam", "kandaisyn", "kandaysyn", "jakshy", "rahmat", "bugun", "emne", "kylyp", "jatasyn", "erteng"},
}


@dataclass
class LanguageGuess:
    code: str  # ISO-ish code, "und" when unknown
    confidence: str  # low | medium | high
    mixed: bool = False
    scripts: dict[str, int] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е").replace("’", "'")
    return text


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(normalize(text))


def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("CYRILLIC"):
            key = "cyrillic"
        elif name.startswith("LATIN"):
            key = "latin"
        elif name.startswith("ARABIC"):
            key = "arabic"
        elif name.startswith(("CJK", "HIRAGANA", "KATAKANA")):
            key = "cjk"
        else:
            key = "other"
        counts[key] = counts.get(key, 0) + 1
    return counts


def detect_language(text: str) -> LanguageGuess:
    s = script_counts(text)
    total = sum(s.values())
    if total == 0:
        return LanguageGuess("und", "low", scripts=s)
    toks = set(tokens(text))
    letters = set(normalize(text))
    mixed = sum(1 for v in s.values() if v / total >= 0.2) > 1 or (
        s.get("cyrillic", 0) and s.get("latin", 0) and min(s["cyrillic"], s["latin"]) >= 3
    )
    dominant = max(s, key=s.get)

    if dominant == "cjk":
        return LanguageGuess("zh", "medium", mixed, s)
    if dominant == "arabic":
        return LanguageGuess("ar", "medium", mixed, s)
    if dominant == "cyrillic":
        if letters & KK_ONLY:
            return LanguageGuess("kk", "high" if len(letters & KK_ONLY) > 1 else "medium", mixed, s)
        if letters & KK_KY_SHARED:
            # Kazakh and Kyrgyz share ү ө ң; without further evidence do not pick one silently.
            return LanguageGuess("ky", "low", mixed, s, candidates=["ky", "kk"])
        return LanguageGuess("ru", "high" if total > 12 else "medium", mixed, s)

    scores = {lang: len(toks & words) for lang, words in STOPWORDS.items()}
    if letters & TR_CHARS:
        scores["tr"] += 2
    if letters & DE_CHARS:
        scores["de"] += 2
    best = sorted(scores.items(), key=lambda kv: -kv[1])
    top, second = best[0], best[1]
    if top[1] == 0:
        return LanguageGuess("und", "low", mixed, s)
    conf = "high" if top[1] >= 2 and top[1] > second[1] else "medium" if top[1] > second[1] else "low"
    cands = [l for l, v in best if v == top[1]] if conf == "low" else []
    return LanguageGuess(top[0], conf, mixed, s, cands)


# --------------------------------------------------------------------------------------------------
# Informal-language lexicon
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Sense:
    meaning: str
    lang: str
    hints: frozenset[str] = frozenset()  # context words that select this sense


def S(meaning: str, lang: str, *hints: str) -> Sense:  # noqa: N802
    return Sense(meaning, lang, frozenset(hints))


LEXICON: dict[str, list[Sense]] = {
    # Russian informal
    "чд": [S("what are you doing", "ru")],
    "че делаешь": [S("what are you doing", "ru")],
    "че как": [S("how are you / how is it going", "ru")],
    "че каво": [S("what's up", "ru")],
    "норм": [S("fine / okay", "ru")],
    "нормас": [S("fine / okay", "ru")],
    "крч": [S("in short", "ru")],
    "имхо": [S("in my humble opinion", "ru")],
    "хз": [S("don't know", "ru")],
    "го": [S("let's go / let's do it", "ru", "кино", "гулять", "сегодня", "вечером", "давай", "тусить", "пойдем", "погнали"),
           S("Go (programming language)", "ru", "golang", "программирование", "код", "backend", "бэкенд", "язык", "программист")],
    "пжл": [S("please", "ru")], "спс": [S("thanks", "ru")], "пож": [S("please / you're welcome", "ru")],
    "ща": [S("right now / in a moment", "ru")], "чел": [S("dude / person", "ru")],
    "кста": [S("by the way", "ru")], "мб": [S("maybe", "ru")], "тож": [S("also / me too", "ru")],
    # English informal
    "wyd": [S("what are you doing", "en")], "brb": [S("be right back", "en")], "idk": [S("I don't know", "en")],
    "ngl": [S("not gonna lie", "en")], "lol": [S("laughing out loud (amusement)", "en")],
    "imo": [S("in my opinion", "en")], "tbh": [S("to be honest", "en")], "omw": [S("on my way", "en")],
    "gonna": [S("going to", "en")], "wanna": [S("want to", "en")], "ty": [S("thank you", "en")],
    "fr": [S("for real", "en", "bro", "ngl", "lol", "literally", "cap", "deadass", "fr fr"),
           S("French (language/country)", "en", "french", "france", "language", "paris", "французский")],
    # Kazakh colloquial
    "қалайсың": [S("how are you", "kk")], "не істеп жатсың": [S("what are you doing", "kk")],
    "жақсы": [S("good / fine", "kk")], "рахмет": [S("thank you", "kk")], "иә": [S("yes", "kk")],
    "болады": [S("okay / it will be / it is possible", "kk")], "қазір": [S("now", "kk")],
    "qalaisyn": [S("how are you", "kk")], "jaqsy": [S("good / fine", "kk")], "rahmet": [S("thank you", "kk")],
    # Kyrgyz colloquial
    "кандайсың": [S("how are you", "ky")], "эмне кылып жатасың": [S("what are you doing", "ky")],
    "жакшы": [S("good / fine", "ky")], "рахмат": [S("thank you", "ky")], "ооба": [S("yes", "ky")],
    "kandaisyn": [S("how are you", "ky")], "jakshy": [S("good / fine", "ky")],
    # transliterated Russian
    "privet": [S("hello", "ru-Latn")], "spasibo": [S("thank you", "ru-Latn")], "xz": [S("don't know", "ru-Latn")],
    "norm": [S("fine / okay", "ru-Latn")], "krch": [S("in short", "ru-Latn")], "chto delaesh": [S("what are you doing", "ru-Latn")],
    "kak dela": [S("how are you", "ru-Latn")], "poydem": [S("let's go", "ru-Latn")],
}

_PHRASES = sorted((k for k in LEXICON if " " in k), key=lambda k: -len(k))
_SINGLE_KEYS = [k for k in LEXICON if " " not in k and len(k) >= 5]


@dataclass
class Annotation:
    span: str
    meaning: str
    lang: str
    fuzzy: bool = False


@dataclass
class Ambiguity:
    span: str
    candidates: list[str]


@dataclass
class Interpretation:
    original: str  # never modified
    language: LanguageGuess
    annotations: list[Annotation]
    ambiguous: list[Ambiguity]
    unresolved_tokens: list[str]


def _pick(senses: list[Sense], context: set[str]) -> Sense | None:
    if len(senses) == 1:
        return senses[0]
    scored = [(len(s.hints & context), s) for s in senses]
    scored.sort(key=lambda x: -x[0])
    if scored[0][0] > 0 and scored[0][0] > scored[1][0]:
        return scored[0][1]
    return None


def interpret(text: str, context: list[str] | None = None) -> Interpretation:
    """Annotate informal expressions. `context` = previous messages, used only to disambiguate."""
    ctx_tokens = set(tokens(text))
    for prev in context or []:
        ctx_tokens |= set(tokens(prev))
    norm = normalize(text)
    ann: list[Annotation] = []
    amb: list[Ambiguity] = []
    claimed: set[str] = set()

    def handle(span: str, senses: list[Sense], fuzzy: bool = False) -> None:
        ctx = ctx_tokens - set(span.split())
        chosen = _pick(senses, ctx)
        if chosen:
            ann.append(Annotation(span, chosen.meaning, chosen.lang, fuzzy))
        else:
            amb.append(Ambiguity(span, [s.meaning for s in senses]))

    for phrase in _PHRASES:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm):
            handle(phrase, LEXICON[phrase])
            claimed.update(phrase.split())

    toks = tokens(text)
    unresolved = []
    for t in toks:
        if t in claimed:
            continue
        if t in LEXICON:
            handle(t, LEXICON[t])
        elif len(t) >= 5:
            close = get_close_matches(t, _SINGLE_KEYS, n=1, cutoff=0.84)
            if close and abs(len(close[0]) - len(t)) <= 1:
                handle(close[0], LEXICON[close[0]], fuzzy=True)
            else:
                unresolved.append(t)
        else:
            unresolved.append(t)
    return Interpretation(text, detect_language(text), ann, amb, unresolved)
