import streamlit as st
import pandas as pd
import json
import re

st.set_page_config(page_title="Excel → JSON Конвертер", layout="wide")
st.title("📋 Excel → JSON Генератор, Валидация и Перенос промтов")

st.sidebar.header("⚙️ Режим работы")
mode = st.sidebar.radio("Выберите режим:", ["Создать JSON", "Проверить Excel vs JSON", "Перенести промты"])

# ----------------- Утилиты -----------------

def normalize_cols(cols):
    return [str(c).strip().replace("\n", "") for c in cols]

def detect_question_column(df):
    candidates = ["№. Вопроса", "№ Вопроса", "Номер вопроса", "Номер", "Номер вопроса"]
    for c in df.columns:
        cn = str(c).lower()
        for pc in candidates:
            if pc.lower() in cn:
                return c
    for c in df.columns:
        if "вопрос" in str(c).lower():
            return c
    return None

def format_qid(raw):
    """Преобразует значение номера вопроса в строковый id:
       - целые (1.0) -> '1'
       - дробные (5.1) -> '5.1'
       - строки -> stripped
       - NaN -> ''
    """
    if pd.isna(raw):
        return ""
    # если уже строка, просто стрим
    if isinstance(raw, str):
        s = raw.strip()
        return s
    # если число
    try:
        # pandas может передавать numpy types
        val = float(raw)
        if val.is_integer():
            return str(int(val))
        # убрать лишние 0 (например 5.100000 -> 5.1)
        s = repr(val)
        # normalize
        s = s.rstrip('0').rstrip('.') if '.' in s else s
        return s
    except Exception:
        return str(raw).strip()

def split_numbered(text):
    if not isinstance(text, str) or not text.strip():
        return None
    if re.search(r'\d+\.', text):
        parts = re.split(r'\d+\.\s*', text)
        texts = [p.strip() for p in parts if p.strip()]
        return texts or None
    return None

def make_prompt(tuning_text, main_text):
    """
    Формирует prompt как список объектов [{"text": ..., "text_chat": ...}].

    text       → из колонки "Текст"
    text_chat  → из колонки "Тюнинг"

    НЕ склеивает, НЕ заменяет одно другим, НЕ использует fallback.
    Каждый пункт берется со своего источника.
    """

    main_text = str(main_text or "").strip()
    tuning_text = str(tuning_text or "").strip()

    # Режем по нумерации ТОЛЬКО в "Текст"
    if re.search(r'\d+\.', main_text):
        items = [i.strip() for i in re.split(r'\d+\.', main_text) if i.strip()]
    else:
        items = [main_text] if main_text else []

    # Режем по нумерации ТОЛЬКО в "Тюнинг"
    if re.search(r'\d+\.', tuning_text):
        items_chat = [i.strip() for i in re.split(r'\d+\.', tuning_text) if i.strip()]
    else:
        items_chat = [tuning_text] if tuning_text else []

    # Если длины совпадают — маппируем поэлементно
    if len(items) == len(items_chat):
        result = []
        for t, c in zip(items, items_chat):
            result.append({"text": t, "text_chat": c})
        return result

    # Если длины не совпали: берем каждое значение как есть
    return [{
        "text": tuning_text,
        "text_chat": main_text,
    }]

def make_variants_prompt(tuning_variants, main_variants):
    """
    Формирует variants_prompt как список объектов [{"text": ..., "text_chat": ...}].

    text       → из "Тюнинг вариантов"
    text_chat  → из "Варианты ответов"

    Поддерживает:
    - список вариантов через переносы строк
    - нумерацию (1., 2., 3.)
    """

    # нормализуем строки
    t = str(tuning_variants or "").strip()
    m = str(main_variants or "").strip()

    # ---------- разрезаем тюнинг ----------
    if not t:
        t_items = []
    elif re.search(r"\d+\.", t):
        t_items = [i.strip() for i in re.split(r"\d+\.", t) if i.strip()]
    else:
        t_items = [i.strip() for i in t.split("\n") if i.strip()]

    # ---------- разрезаем основной текст ----------
    if not m:
        m_items = []
    elif re.search(r"\d+\.", m):
        m_items = [i.strip() for i in re.split(r"\d+\.", m) if i.strip()]
    else:
        m_items = [i.strip() for i in m.split("\n") if i.strip()]

    # ---------- если одинаковая длина ----------
    if len(t_items) == len(m_items) and len(t_items) > 0:
        return [
            {"text": t_val, "text_chat": m_val}
            for t_val, m_val in zip(t_items, m_items)
        ]

    # ---------- fallback ----------
    if t or m:
        return [{
            "text": t,
            "text_chat": m
        }]

    return []

def parse_answers_from_excel(value):
    """Парсим пары key:value из ячейки (каждая строка) -> возвращаем dict или None"""
    if not isinstance(value, str) or not value.strip():
        return None
    lines = [ln.strip() for ln in value.split("\n") if ln.strip()]
    res = {}
    for ln in lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            res[k.strip()] = v.strip()
    return res if res else None

def id_key_for_sort(item):
    """
    Возвращает кортеж для сортировки:
    '1' -> (1, 0)
    '5.1' -> (5, 1)
    строковые id идут в конец -> (999999, 'string')
    """
    raw = item.get("question_id", "")
    if raw is None:
        raw = ""
    raw = str(raw).strip()
    m = re.match(r'^(\d+)(?:\.(\d+))?$', raw)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else 0
        return (major, minor)
    # put non-numeric ids after numeric ones, keep stable by raw
    return (999999, raw)

# ----------------- Генерация JSON -----------------

def generate_json_from_df(df):
    df = df.copy()
    df.columns = normalize_cols(df.columns)

    qcol = detect_question_column(df)
    if not qcol:
        st.error("Не найден столбец номера вопроса (например 'Номер вопроса' или '№. Вопроса').")
        return None, ["Не найден столбец номера вопроса"]

    type_mapping = {
        "рейтинг": "rating",
        "подвопросы": "subquestions",
        "да/нет": "yes_no",
        "варианты": "variants",
        "варианты с иное": "variants_with_other",
        "возраст": "age",
        "пол": "gender",
        "город": "city"
    }

    rows = []
    for _, row in df.iterrows():
        raw = row.get(qcol, "")
        qid = format_qid(raw)
        rows.append((qid, row))

    final = []
    parents_map = {}
    warnings = []

    # 1. Создаём основные вопросы
    for qid, row in rows:
        if not qid:
            warnings.append("Пропущена строка с пустым номером вопроса.")
            continue
        if "." in qid:
            continue
        type_rus = str(row.get("Тип вопроса", "")).strip().lower()
        q_type = type_mapping.get(type_rus, type_rus)

        question = {
            "question_id": qid,
            "type_questions": q_type,
            "is_rotation": str(row.get("Ротация", "")).strip().lower() == "true",
            "nlu_tag": str(row.get("Тег", "")).strip() or None,
            "answers": {
                "intents": parse_answers_from_excel(row.get("Интенты", "")),
                "entities": parse_answers_from_excel(row.get("Сущности", ""))
            },
            "prompt": make_prompt(row.get("Тюнинг", ""), row.get("Текст", "")),
            "navigation": None,
            "is_depending_questions": None,
            "visible": True,
            "need_stop": None,
            "stop_ask": None,
            "stop_count": None,
            "no_answer": None,
            "need_replaced": False,
            "several_variants": None,
            "need_sbg": False
        }

        if q_type == "rating":
            question.update({"rating_grammar": None, "max_rate": None, "isZero": None})
        if q_type in ["variants", "variants_with_other"]:
            variants_text = str(row.get("Варианты ответов", "") or "").strip()
            question.update({
                "sound_variants": True,
                "variants_prompt": [{"text": variants_text, "text_chat": variants_text}] if variants_text else []
            })
        if q_type == "yes_no":
            question.update({"is_additional_other": False})

        # next_question_id: пробуем инкремент по целому
        try:
            next_id = str(int(qid) + 1)
        except Exception:
            next_id = None
        question["next_question_id"] = next_id

        final.append(question)
        parents_map[qid] = question

    # 2. Прикрепляем подвопросы (точная обработка: используем собственную строку подвопроса)
    for qid, row in rows:
        if not qid or "." not in qid:
            continue
        base = qid.split(".")[0]
        parent = parents_map.get(base)
        if parent is None:
            warnings.append(f"Подвопрос {qid}: родитель {base} не найден — пропущен.")
            continue

        type_rus = str(row.get("Тип вопроса", "")).strip().lower()
        q_type = type_mapping.get(type_rus, type_rus)

        sub = {
            "subquestion_id": qid,
            "type_questions": q_type,
            "is_rotation": str(row.get("Ротация", "")).strip().lower() == "true",
            "nlu_tag": str(row.get("Тег", "")).strip() or None,
            "answers": {
                "intents": parse_answers_from_excel(row.get("Интенты", "")),
                "entities": parse_answers_from_excel(row.get("Сущности", ""))
            },
            "prompt": make_prompt(row.get("Тюнинг", ""), row.get("Текст", "")),
            "navigation": None,
            "is_depending_questions": None,
            "visible": True,
            "need_stop": None,
            "stop_ask": None,
            "stop_count": None,
            "no_answer": None,
            "need_replaced": False,
            "several_variants": None,
            "need_sbg": False
        }

        if q_type == "rating":
            sub.update({"rating_grammar": None, "max_rate": None, "isZero": None})

        if q_type in ["variants", "variants_with_other"]:
            main_variants = row.get("Варианты ответов", "")
            tuning_variants = row.get("Тюнинг вариантов", "")
            question.update({
                "sound_variants": True,
                "variants_prompt": make_variants_prompt(tuning_variants, main_variants)
            })

        if q_type == "yes_no":
            sub.update({"is_additional_other": False})

        if "subquestions" not in parent:
            parent["subquestions"] = []
        parent["subquestions"].append(sub)

    # сортируем верхний уровень корректно
    final_sorted = sorted(final, key=id_key_for_sort)
    return [{"0": final_sorted}], warnings

# ------------- Перенос промтов ----------------

def update_prompts_in_json_hard(existing_json, df):
    df = df.copy()
    df.columns = normalize_cols(df.columns)
    qcol = detect_question_column(df)
    if not qcol:
        return None, ["Не найден столбец номера вопроса"]

    mapping = {}
    for _, row in df.iterrows():
        raw = row.get(qcol, "")
        qid = format_qid(raw)
        if qid:
            mapping[qid] = row

    updated = json.loads(json.dumps(existing_json))  # deep copy
    if not isinstance(updated, list) or not updated or not isinstance(updated[0], dict):
        return None, ["Неверный формат входного JSON. Ожидается [{'0': [...]}]"]

    questions = updated[0].get("0", [])
    warnings = []

    for q in questions:
        qid = q.get("question_id")
        if qid:
            row = mapping.get(str(qid))
            if row is not None:
                q["prompt"] = make_prompt(row.get("Тюнинг", ""), row.get("Текст", ""))
                if q.get("type_questions") in ["variants", "variants_with_other"]:
                    vt = str(row.get("Варианты ответов", "") or "").strip()
                    q["variants_prompt"] = [{"text": vt, "text_chat": vt}] if vt else []
            else:
                # заменим на пустой prompt/variants_prompt
                q["prompt"] = []
                if q.get("type_questions") in ["variants", "variants_with_other"]:
                    q["variants_prompt"] = []

        if "subquestions" in q:
            for sq in q["subquestions"]:
                sqid = sq.get("subquestion_id")
                if sqid:
                    row = mapping.get(str(sqid))
                    if row is not None:
                        sq["prompt"] = make_prompt(row.get("Тюнинг", ""), row.get("Текст", ""))
                        if sq.get("type_questions") in ["variants", "variants_with_other"]:
                            vt = str(row.get("Варианты ответов", "") or "").strip()
                            sq["variants_prompt"] = [{"text": vt, "text_chat": vt}] if vt else []
                    else:
                        sq["prompt"] = []
                        if sq.get("type_questions") in ["variants", "variants_with_other"]:
                            sq["variants_prompt"] = []

    return updated, warnings

# ----------------- UI -----------------

uploaded_excel = st.file_uploader("📎 Загрузите Excel", type=["xlsx", "xls"])
df = None
sheet_name = None
if uploaded_excel:
    excel = pd.ExcelFile(uploaded_excel)
    sheet_name = st.selectbox("📑 Выберите лист", excel.sheet_names)
    df = pd.read_excel(excel, sheet_name=sheet_name)
    st.subheader("📄 Первые 10 строк Excel")
    st.dataframe(df.head(10))

# Режим: Создать JSON
if mode == "Создать JSON":
    if df is None:
        st.info("Загрузите Excel лист, чтобы сгенерировать JSON.")
    else:
        col1, col2 = st.columns([1, 1])
        gen = col1.button("🛠 Сгенерировать JSON")
        dl_place = col2.empty()

        if gen:
            json_data, warnings = generate_json_from_df(df)
            if json_data is None:
                st.error("Ошибка генерации JSON.")
            else:
                st.success("✅ JSON создан.")
                if warnings:
                    st.warning("\n".join(warnings))
                st.subheader("🔍 Превью (первые 10 вопросов)")
                st.json(json_data[0].get("0", [])[:10])
                b = json.dumps(json_data, ensure_ascii=False, indent=2).encode("utf-8")
                dl_place.download_button("💾 Скачать JSON", b, file_name=f"{sheet_name}.json", mime="application/json")

# Режим: Проверить Excel vs JSON (упрощённо)
if mode == "Проверить Excel vs JSON":
    uploaded_json = st.file_uploader("📎 Загрузите JSON для проверки", type=["json"])
    if uploaded_json and df is not None:
        if st.button("🔎 Проверить"):
            try:
                json_data = json.load(uploaded_json)
            except Exception as e:
                st.error(f"Ошибка чтения JSON: {e}")
                json_data = None
            if json_data:
                qcol = detect_question_column(df)
                excel_ids = set(format_qid(x) for x in df[qcol].dropna().tolist())
                json_ids = set()
                try:
                    for q in json_data[0].get("0", []):
                        if q.get("question_id"):
                            json_ids.add(str(q.get("question_id")))
                        if "subquestions" in q:
                            for s in q["subquestions"]:
                                if s.get("subquestion_id"):
                                    json_ids.add(str(s.get("subquestion_id")))
                    only_in_excel = sorted([x for x in excel_ids if x not in json_ids])
                    only_in_json = sorted([x for x in json_ids if x not in excel_ids])
                    st.write("Только в Excel (первые 50):", only_in_excel[:50])
                    st.write("Только в JSON (первые 50):", only_in_json[:50])
                    if not only_in_excel and not only_in_json:
                        st.success("✅ Совпадают id вопросов (упрощённая проверка).")
                except Exception as e:
                    st.error(f"Ошибка при проверке: {e}")

# Режим: Перенести промты
if mode == "Перенести промты":
    uploaded_json = st.file_uploader("📎 Загрузите существующий JSON (будет обновлён)", type=["json"], key="upd_json")
    if uploaded_json and df is not None:
        if st.button("🔁 Перенести промты"):
            try:
                existing_json = json.load(uploaded_json)
            except Exception as e:
                st.error(f"Ошибка чтения JSON: {e}")
                existing_json = None
            if existing_json:
                updated, warnings = update_prompts_in_json_hard(existing_json, df)
                if updated is None:
                    st.error("Не удалось обновить JSON (проверьте формат).")
                else:
                    st.success("✅ Промты обновлены.")
                    if warnings:
                        st.warning("\n".join(warnings))
                    st.subheader("🔍 Превью обновлённого JSON (первые 10 вопросов)")
                    st.json(updated[0].get("0", [])[:10])
                    b = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8")
                    st.download_button("💾 Скачать обновлённый JSON", b, file_name=f"updated_{sheet_name}.json", mime="application/json")
