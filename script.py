import streamlit as st
import pandas as pd
import json
import re

st.set_page_config(page_title="Excel → JSON Конвертер", layout="wide")
st.title("📋 Excel → JSON Генератор, Валидация и Перенос промтов")

st.sidebar.header("⚙️ Режим работы")
mode = st.sidebar.radio("Выберите режим:", ["Создать JSON", "Проверить Excel vs JSON", "Перенести промты"])

# ================================================================
#                     ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ================================================================

def make_prompt(tuning_text, main_text):
    """Формирование prompt с разделением по формату '1.'"""
    tuning_text = str(tuning_text or "").strip()
    main_text = str(main_text or "").strip()

    def split_if_numbered(text):
        if re.search(r'\d+\.', text):
            parts = re.split(r'\d+\.', text)
            return [p.strip() for p in parts if p.strip()]
        return None

    numbered = split_if_numbered(tuning_text) or split_if_numbered(main_text)
    if numbered:
        return [{"text": t, "text_chat": t} for t in numbered]

    combined = tuning_text if tuning_text else main_text
    return [{"text": combined, "text_chat": combined}] if combined else []


def parse_answers_from_excel(value):
    """Парсим интенты и сущности в виде key:value"""
    if not isinstance(value, str) or not value.strip():
        return None
    lines = [line.strip() for line in value.split("\n") if line.strip()]
    result = {}
    for line in lines:
        if ":" in line:
            key, val = line.split(":", 1)
            result[key.strip()] = val.strip()
    return result if result else None


def detect_question_column(df):
    """Находим корректный столбец № вопроса"""
    possible = ["№. Вопроса", "№ Вопроса", "Номер вопроса", "Номер вопроса "]
    return next((c for c in df.columns if c.strip() in possible), None)


# ================================================================
#                    ГЕНЕРАЦИЯ JSON ИЗ EXCEL
# ================================================================

def generate_json_from_df(df):
    final = []
    warnings = []

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

    df.columns = [c.strip().replace("\n", "") for c in df.columns]

    question_col = detect_question_column(df)
    if not question_col:
        st.error("❌ Не найден столбец с номерами вопросов")
        return [], []

    for _, row in df.iterrows():
        raw_id = str(row.get(question_col, "")).strip()
        q_id = raw_id

        type_rus = str(row.get("Тип вопроса", "")).strip().lower()
        q_type = type_mapping.get(type_rus, type_rus)

        question = {
            "question_id": None if "." in q_id else q_id,
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
            question.update({"rating_grammar": None, "max_rate": None, "is_zero": None})

        if q_type in ["variants", "variants_with_other"]:
            variants_text = str(row.get("Варианты ответов", "")).strip()
            question.update({
                "sound_variants": True,
                "variants_prompt": [{"text": variants_text, "text_chat": variants_text}] if variants_text else []
            })

        if q_type == "yes_no":
            question.update({"is_additional_other": False})

        # -------------------- обычный вопрос --------------------
        if "." not in q_id:
            try:
                next_id = str(int(q_id) + 1)
            except:
                next_id = None
            question["next_question_id"] = next_id
            final.append(question)

        else:
            # -------------------- подвопрос --------------------
            base_id = q_id.split('.')[0]
            question_sub = question.copy()
            question_sub.pop("question_id", None)
            question_sub["subquestion_id"] = q_id

            parent = next((q for q in final if q["question_id"] == base_id), None)
            if not parent:
                warnings.append(f"⚠ Подвопрос {q_id} не найден родитель {base_id}")
                continue

            if "subquestions" not in parent:
                parent["subquestions"] = []
            parent["subquestions"].append(question_sub)

    return [{"0": final}], warnings


# ================================================================
#            ПЕРЕНОС PROMPT / VARIANTS_PROMPT В JSON
# ================================================================

def update_prompts_in_json(existing_json, df):
    warnings = []

    question_col = detect_question_column(df)
    if not question_col:
        return None, ["❌ Не найден столбец № вопроса"]

    df["__qid"] = df[question_col].astype(str).str.strip()

    excel_map = {row["__qid"]: row for _, row in df.iterrows()}
    questions = existing_json[0]["0"]

    for q in questions:
        if not q.get("question_id"):
            continue

        base_id = q["question_id"]

        if base_id in excel_map:
            row = excel_map[base_id]

            q["prompt"] = make_prompt(row.get("Тюнинг", ""), row.get("Текст", ""))

            if q.get("type_questions") in ["variants", "variants_with_other"]:
                variants_text = str(row.get("Варианты ответов", "")).strip()
                q["variants_prompt"] = [{"text": variants_text, "text_chat": variants_text}] if variants_text else []

        if "subquestions" in q:
            for sq in q["subquestions"]:
                sq_base = sq["subquestion_id"].split(".")[0]
                if sq_base in excel_map:
                    row = excel_map[sq_base]

                    sq["prompt"] = make_prompt(row.get("Тюнинг", ""), row.get("Текст", ""))

                    if sq.get("type_questions") in ["variants", "variants_with_other"]:
                        variants_text = str(row.get("Варианты ответов", "")).strip()
                        sq["variants_prompt"] = [{"text": variants_text, "text_chat": variants_text}] if variants_text else []

    return existing_json, warnings


# ================================================================
#                          UI
# ================================================================

uploaded_excel = st.file_uploader("📎 Загрузите Excel", type=["xlsx", "xls"])
if uploaded_excel:
    excel = pd.ExcelFile(uploaded_excel)
    sheet_name = st.selectbox("📑 Выберите лист", excel.sheet_names)
    df = pd.read_excel(excel, sheet_name=sheet_name)

    st.subheader("📄 Первые 10 строк Excel")
    st.dataframe(df.head(10))


# ============================
#         РЕЖИМ 1
# ============================

if mode == "Создать JSON" and uploaded_excel:
    col1, col2 = st.columns([1, 1])
    gen_btn = col1.button("🛠 Сгенерировать JSON")
    dl_place = col2.empty()

    if gen_btn:
        json_data, warnings = generate_json_from_df(df)
        st.success("✅ JSON создан!")

        if warnings:
            st.warning("\n".join(warnings))

        st.subheader("🔍 JSON (первые 10)")
        st.json(json_data[0]["0"][:10])

        dl_place.download_button(
            "💾 Скачать JSON",
            json.dumps(json_data, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"{sheet_name}.json",
            mime="application/json"
        )


# ============================
#         РЕЖИМ 3
# ============================

if mode == "Перенести промты" and uploaded_excel:
    uploaded_json = st.file_uploader("📎 Загрузите JSON для обновления", type=["json"])

    if uploaded_json:
        if st.button("🔁 Перенести промты"):
            try:
                existing_json = json.load(uploaded_json)
            except:
                st.error("❌ Ошибка чтения JSON")
                st.stop()

            updated, warnings = update_prompts_in_json(existing_json, df)

            if updated:
                st.success("✅ Промты обновлены!")

                if warnings:
                    st.warning("\n".join(warnings))

                st.subheader("🔍 Обновлённый JSON (первые 10)")
                st.json(updated[0]["0"][:10])

                st.download_button(
                    "💾 Скачать обновлённый JSON",
                    json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8"),
                    file_name="updated_prompts.json",
                    mime="application/json"
                )
