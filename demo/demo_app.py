import streamlit as st
import pandas as pd
from src.program1_integration import run_reviewed_program1
import json
from google import genai


# Add a title to your app
st.title("Program 1 Demo")

filepathSAS = "manifests/program1.json"
with open(filepathSAS, "r", encoding="utf-8") as file:
    code_content = file.read()
# Add some descriptive text
st.write("SAS Code")
# 3. Display it using st.code
st.code(code_content, language="SAS", line_numbers=True)

filepathgrp = "output/program1_reviewed_code/grp_score_macro.py"
with open(filepathgrp, "r", encoding="utf-8") as file:
    code_content = file.read()
st.write("Python Code: grp_score_macro.py")
# 3. Display it using st.code
st.code(code_content, language="python", line_numbers=True)

filepathom = "output/program1_reviewed_code/om.py"
with open(filepathom, "r", encoding="utf-8") as file:
    code_content = file.read()
st.write("Python Code: om.py")
# 3. Display it using st.code
st.code(code_content, language="python", line_numbers=True)

filepathor = "output/program1_reviewed_code/or.py"
with open(filepathor, "r", encoding="utf-8") as file:
    code_content = file.read()
st.write("Python Code: or.py")
# 3. Display it using st.code
st.code(code_content, language="python", line_numbers=True)

filepathos = "output/program1_reviewed_code/os.py"
with open(filepathos, "r", encoding="utf-8") as file:
    code_content = file.read()
st.write("Python Code: os.py")
# 3. Display it using st.code
st.code(code_content, language="python", line_numbers=True)

filepathproc = "output/program1_reviewed_code/process.py"
with open(filepathproc, "r", encoding="utf-8") as file:
    code_content = file.read()
st.write("Python Code: process.py")
# 3. Display it using st.code
st.code(code_content, language="python", line_numbers=True)

filepathptex = "output/program1_reviewed_code/ptexp.py"
with open(filepathptex, "r", encoding="utf-8") as file:
    code_content = file.read()
st.write("Python Code: ptexp.py")
# 3. Display it using st.code
st.code(code_content, language="python", line_numbers=True)


result = run_reviewed_program1()
st.subheader(f"Validation results: {result['status']}")

validation_rows = []
for output_table, output_result in result["outputs"].items():
    validation = output_result.get("validation", {})
    mismatches = validation.get("mismatches", {})
    validation_rows.append({
        "Output": output_table,
        "Status": output_result["status"],
        "Python rows": validation.get("python_rows"),
        "SAS rows": validation.get("sas_rows"),
        "Rows match": validation.get("row_count_match"),
        "Column order matches": validation.get("column_order_match"),
        "Missing columns": len(validation.get("missing_columns", [])),
        "Extra columns": len(validation.get("extra_columns", [])),
        "Key issues": (
            validation.get("blank_python_keys", 0)
            + validation.get("blank_sas_keys", 0)
            + len(validation.get("duplicate_python_ids", []))
            + len(validation.get("duplicate_sas_ids", []))
            + len(validation.get("missing_ids", []))
            + len(validation.get("extra_ids", []))
        ),
        "Mismatched columns": len(mismatches),
        "Mismatched cells": sum(
            detail["count"] for detail in mismatches.values()
        ),
    })

st.dataframe(
    pd.DataFrame(validation_rows),
    hide_index=True,
    use_container_width=True,
)

for output_table, output_result in result["outputs"].items():
    validation = output_result.get("validation", {})
    mismatches = validation.get("mismatches", {})
    if mismatches:
        with st.expander(f"{output_table} mismatch details"):
            mismatch_rows = [
                {
                    "Column": column,
                    "Mismatched cells": details["count"],
                }
                for column, details in mismatches.items()
            ]
            st.dataframe(
                pd.DataFrame(mismatch_rows),
                hide_index=True,
                use_container_width=True,
            )
            for column, details in mismatches.items():
                st.caption(f"Examples for {column}")
                st.dataframe(
                    pd.DataFrame(details["examples"]),
                    hide_index=True,
                    use_container_width=True,
                )

client = genai.Client(api_key=st.secrets["api_key"])

@st.cache_data
def get_table_analysis(data_json):
  
  response = client.models.generate_content(
      model="gemini-3.5-flash-lite",
      contents = f"""
          For this project, we are working on developing a pipeline to translate legacy SAS code into
          python code. We have attempted the first portion of our project, and the results are in the table.
          Generate a brief analysis of the accuracy of the translation, a brief explanation of the results
          and possible reasons for them, and a short explanation of what our program does.
          {data_json}
          """
  )
  return response.candidates[0].content.parts[0].text

data_string =  result = json.dumps(result, indent=2, default=str)

# 3. Render the static AI-generated text output field
st.subheader("AI Data Analysis")
with st.container(border=True):
  try:
    with st.spinner("Generating analysis..."):
      analysis_text = get_table_analysis(data_string)
    st.markdown(analysis_text)
  except Exception as e:
    st.error(f"Could not load AI analysis: {e}")
