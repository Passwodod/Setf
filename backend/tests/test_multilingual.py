import uuid

import pytest
from sqlalchemy import text

from app.ai.concepts import match_concepts, search_terms
from app.ai.language import detect_language, interpret
from app.core.db import get_engine
from app.services.search import build_tsquery, search_messages


def meanings(t, ctx=None):
    r = interpret(t, ctx)
    return {a.span: a.meaning for a in r.annotations}


# ---- language detection ----------------------------------------------------------------------
@pytest.mark.parametrize("text_,code", [
    ("Привет, как дела сегодня? Что делаешь вечером", "ru"),
    ("What are you doing tonight, let's go to the movies", "en"),
    ("Бүгін мен жұмыстамын, қалайсың? Рахмет", "kk"),
    ("Bugün çok yorgunum ve hiçbir şey yapmak istemiyorum", "tr"),
    ("Ich bin heute nicht im Büro und das ist gut", "de"),
    ("Je suis à la maison avec une amie aujourd'hui", "fr"),
    ("Estoy en casa hoy y no quiero salir con los demás", "es"),
    ("我今天喜欢学习编程", "zh"),
    ("أنا أحب البرمجة كثيرا", "ar"),
    ("privet kak dela, chto delaesh segodnya", "ru-Latn"),
    ("salem, qalaisyn? bugin jaqsy", "kk-Latn"),
])
def test_language_detection(text_, code):
    assert detect_language(text_).code == code


def test_kazakh_kyrgyz_ambiguity_not_guessed_silently():
    g = detect_language("Мен үйдөмүн")  # only shared letters: no confident pick
    assert g.confidence == "low" and set(g.candidates) == {"ky", "kk"}


def test_code_switching_flagged_as_mixed():
    for t in ["Брат, let's go туда", "Я сегодня working from home", "Бұл өте interesting", "Эртең meeting болады"]:
        assert detect_language(t).mixed, t


# ---- informal language -----------------------------------------------------------------------
def test_russian_slang():
    m = meanings("чд, норм? крч хз")
    assert m["чд"] == "what are you doing" and m["норм"].startswith("fine")
    assert m["крч"] == "in short" and m["хз"] == "don't know"
    assert meanings("че делаешь")["че делаешь"] == "what are you doing"
    assert meanings("Чё как?")["че как"].startswith("how are you")


def test_english_slang():
    m = meanings("wyd? idk ngl, brb lol")
    assert m == {"wyd": "what are you doing", "idk": "I don't know", "ngl": "not gonna lie",
                 "brb": "be right back", "lol": "laughing out loud (amusement)"}


def test_kazakh_and_kyrgyz_colloquial():
    assert meanings("қалайсың? рахмет")["қалайсың"] == "how are you"
    assert meanings("не істеп жатсың")["не істеп жатсың"] == "what are you doing"
    assert meanings("кандайсың? жакшы")["кандайсың"] == "how are you"
    assert meanings("эмне кылып жатасың")["эмне кылып жатасың"] == "what are you doing"


def test_transliteration_slang():
    assert meanings("privet")["privet"] == "hello"
    assert meanings("kak dela")["kak dela"] == "how are you"
    assert meanings("xz")["xz"] == "don't know"


def test_typos_resolved_fuzzily_and_flagged():
    r = interpret("spasiba, qalaysyn")  # typo'd spasibo
    assert any(a.fuzzy and a.meaning == "thank you" for a in r.annotations)


def test_ambiguous_expression_not_invented():
    r = interpret("го")
    assert [a.span for a in r.ambiguous] == ["го"] and len(r.ambiguous[0].candidates) == 2
    assert not r.annotations and r.original == "го"
    r = interpret("fr")
    assert r.ambiguous and len(r.ambiguous[0].candidates) == 2


def test_context_disambiguates():
    assert meanings("го", ["давай сегодня вечером в кино"])["го"].startswith("let's go")
    assert meanings("го", ["пишу backend на golang"])["го"].startswith("Go (programming")
    assert meanings("fr", ["that movie was insane lol"])["fr"] == "for real"
    assert meanings("fr", ["I'm learning French in Paris"])["fr"].startswith("French")


def test_original_text_preserved_and_unknown_not_dropped():
    t = "Слушай bro, крч zxqvw"
    r = interpret(t)
    assert r.original == t and "zxqvw" in r.unresolved_tokens


# ---- meaning extraction: concepts across languages -------------------------------------------
@pytest.mark.parametrize("t", [
    "Программирование", "Programming", "Код жазу", "Программалоо",
    "Programlama", "Programmierung", "programación", "我喜欢编程", "أحب البرمجة",
    "programmirovanie", "programirovaniem",
])
def test_same_concept_across_languages_and_translit(t):
    assert "programming" in {m.concept for m in match_concepts(t)}, t


def test_typo_still_matches_and_is_marked_fuzzy():
    ms = match_concepts("я люблю прогрмаирование")
    assert ms and ms[0].concept == "programming" and ms[0].fuzzy


def test_stance_interest_mention_negative():
    s = lambda t: {m.concept: m.stance for m in match_concepts(t)}  # noqa: E731
    assert s("я очень люблю программирование")["programming"] == "interest"
    assert s("Бүгін футбол болады")["football"] == "mention"
    assert s("ненавижу футбол")["football"] == "negative"
    assert s("мен бизнесті жек көрем")["business"] == "negative"
    assert s("I'm really into investing")["investing"] == "interest"
    assert s("Эртең meeting болады") == {}


def test_no_false_positive_from_short_words():
    assert match_concepts("мама и папа пошли гулять") == []
    assert match_concepts("pair of shoes") == []


def test_code_switched_sentence_extracts_whole_meaning():
    ms = {m.concept: m.stance for m in match_concepts("Брат, я сейчас learning programming, очень interested")}
    assert ms == {"programming": "interest"}


# ---- cross-language search -------------------------------------------------------------------
def test_query_expands_across_languages():
    terms = search_terms("Найди людей, интересующихся бизнесом")["business"]
    assert {"business", "кәсіпкерлік", "ишкерлик"} <= set(terms)
    assert build_tsquery("зззз") is not None  # unknown words still searchable


async def test_russian_query_finds_english_kazakh_kyrgyz_translit_messages():
    async with get_engine().begin() as conn:
        uid, gid = uuid.uuid4(), uuid.uuid4()
        await conn.execute(text("INSERT INTO users(id,email,password_hash) VALUES (:i,'s@x.io','h')"), {"i": uid})
        await conn.execute(text("INSERT INTO groups(id,owner_id,telegram_chat_id,title) VALUES (:g,:o,-1,'G')"), {"g": gid, "o": uid})
        rows = ["Мен кәсіпкерлікпен айналысамын", "I want to start my own business", "Мен ишкерлик менен алектенем",
                "biznes ашкым келет", "Сегодня хорошая погода", "бизнес — это круто"]
        for i, t in enumerate(rows):
            await conn.execute(text("INSERT INTO messages(id,owner_id,group_id,telegram_message_id,text,sent_at) VALUES (gen_random_uuid(),:o,:g,:i,:t,now())"),
                               {"o": uid, "g": gid, "i": i, "t": t})
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        hits = await search_messages(s, uid, "Найди людей, интересующихся бизнесом")
        found = {h.text for h in hits}
        assert {rows[0], rows[1], rows[2], rows[3], rows[5]} <= found
        assert rows[4] not in found
        assert all("business" in h.concepts for h in hits)
        other = await search_messages(s, uuid.uuid4(), "бизнес")
        assert other == []  # other owners see nothing
