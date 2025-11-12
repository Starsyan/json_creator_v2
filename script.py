import streamlit as st
import pandas as pd
import json
import re

st.set_page_config(page_title="Excel → JSON Конвертер", layout="wide")
st.title("📋 Excel → JSON Генератор и Валидация")

st.sidebar.header("⚙️ Режим работы")
mode = st.sidebar.radio("Выберите режим:", ["Создать JSON", "Проверить Excel vs JSON"])

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def make_prompt(tuning_text, main_text):
    """Делит на элементы массива только если есть цифра. без дублирования"""
    tuning_text = str(tuning_text or "").strip()
    main_text = str(main_text or "").strip()

    if re.search(r'\d+\.', tuning_text):
        parts = re.split(r'\d+\.', tuning_text)
        texts = [p.strip() for p in parts if p.strip()]
        return [{"text": t, "text_chat": t} for t in texts]
    elif re.search(r'\d+\.', main_text):
        parts = re.split(r'\d+\.', main_text)
        texts = [p.strip() for p in parts if p.strip()]
        return [{"text": t, "text_chat": t} for t in texts]
    else:
        combined = tuning_text if tuning_text else main_text
        return [{"text": combined, "text_chat": combined}] if combined else []

def parse_answers_from_excel(value):
    """
    Берёт строки вида ключ:значение, возвращает объект {ключ1:значение1, ключ2:значение2,...}
    Если пусто, возвращает None
    """
    if not isinstance(value, str) or not value.strip():
        return None
    lines = [line.strip() for line in value.split("\n") if line.strip()]
    result = {}
    for idx, line in enumerate(lines, start=1):
        if ":" in line:
            key, val = line.split(":", 1)
            result[key.strip()] = val.strip()
        else:
            result[str(idx)] = line
    return result if result else None

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

    possible_question_cols = ["№. Вопроса", "№ Вопроса", "Номер вопроса", "Номер вопроса "]
    question_col = next((c for c in df.columns if c.strip() in possible_question_cols), None)
    if not question_col:
        st.error("❌ Не найден столбец с номерами вопросов.")
        return [], []

    for _, row in df.iterrows():
        raw_id = row.get(question_col, "")
        if isinstance(raw_id, float) and raw_id.is_integer():
            q_id = str(int(raw_id))
        else:
            q_id = str(raw_id).strip()

        type_rus = str(row.get("Тип вопроса", "")).strip().lower()
        q_type = type_mapping.get(type_rus, type_rus)

        # Основной вопрос
        question = {
            "question_id": q_id,
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
            "severel_variants": None,
            "need_sbg": False
        }

        if q_type == "rating":
            question.update({"rating_grammar": None, "max_rate": None, "is_zero": None})
        elif q_type in ["variants", "variants_with_other"]:
            variants_text = str(row.get("Варианты ответов", "")).strip() or ""
            question.update({
                "sound_variants": True,
                "variatns_prompt": [{"text": variants_text, "text_chat": variants_text}] if variants_text else []
            })
        elif q_type == "yes_no":
            question.update({"is_additional_other": False})

        # Основной вопрос или подвопрос
        if "." not in q_id:
            try:
                next_id = str(int(float(re.sub(r"[^\d]", "", q_id))) + 1)
            except:
                next_id = None
            question["next_question_id"] = next_id
            final.append(question)
        else:
            base_id = q_id.split('.')[0]
            question_sub = question.copy()
            question_sub.pop("question_id", None)
            question_sub["subquestion_id"] = q_id
            parent = next((q for q in final if q["question_id"] == base_id), None)
            if parent:
                if "subquestions" not in parent:
                    parent["subquestions"] = []
                parent["subquestions"].append(question_sub)
            else:
                warnings.append(f"⚠️ Подвопрос {q_id} без основного вопроса {base_id}")

    def id_key(item):
        v = str(item.get("question_id", "")).strip()
        m = re.match(r'^(\d+(?:\.\d+)?)', v)
        if m:
            try:
                return float(m.group(1))
            except:
                pass
        return v.lower()

    final_sorted = sorted(final, key=id_key)
    return [{"0": final_sorted}], warnings

# ========================== UI ==========================

uploaded_excel = st.file_uploader("📎 Загрузите Excel файл", type=["xlsx", "xls"])
if uploaded_excel:
    excel = pd.ExcelFile(uploaded_excel)
    sheet_name = st.selectbox("📑 Выберите лист", excel.sheet_names)
    df = pd.read_excel(excel, sheet_name=sheet_name)

    st.subheader("📄 Первые 10 строк Excel")
    st.dataframe(df.head(10))

    # ================== Кнопки ==================
    col1, col2 = st.columns([1,1])
    generate_json = col1.button("🛠 Сгенерировать JSON")
    download_json_placeholder = col2.empty()  # сюда вставим кнопку скачивания позже

    if generate_json:
        json_data, warnings = generate_json_from_df(df)
        if not json_data:
            st.stop()
        st.success("✅ JSON успешно создан!")

        if warnings:
            st.warning("\n".join(warnings))

        st.subheader("🔍 JSON (первые 10 элементов)")
        st.json(json_data[0]["0"][:10])

        # Кнопка скачивания
        json_bytes = json.dumps(json_data, ensure_ascii=False, indent=2).encode("utf-8")
        download_json_placeholder.download_button(
            label="💾 Скачать JSON",
            data=json_bytes,
            file_name=f"{sheet_name}.json",
            mime="application/json"
        )
