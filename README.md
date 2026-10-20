# AI Studio Challenge Project Title

> 💡 **Note for the team:** This is just a template. Update the above title with your AI Studio Challenge Project name. Remove all guidance notes and example text in this template and populate this README with your own content. You can work on this README throughout AI Studio, and get feedback from your AI Studio Coach and Challenge Advisor before finalizing it.  

---

### 👥 **Team Members**

**Example:**

| Name             | GitHub Handle | Contribution                                                             |
|------------------|---------------|--------------------------------------------------------------------------|
| George Pulickan  | @George-Pulickan |          |
| Hao Li.          | @becomingWise |             |
| Lizzette Rivera  | @             |             |
| Pawanjit Birdi   | @             |             |
| Miguel Garcia    | @miguelagarcia-dev|             |
| Shane Salvador   | @             |             |

---

## 🎯 **Project Highlights**

**Example:**

- Developed a machine learning model using `[model type/technique]` to address `[challenge project task]`.
- Achieved `[key metric or result]`, demonstrating `[value or impact]` for `[host company]`.
- Generated actionable insights to inform business decisions at `[host company or stakeholders]`.
- Implemented `[specific methodology]` to address industry constraints or expectations.

---

## 👩🏽‍💻 **Setup and Installation**

Requires Python 3.11+ (developed on 3.12).

```bash
# 1. Clone
git clone https://github.com/Break-Through-Tech/Consulting-1D-ai-powered-sas-migration.git
cd Consulting-1D-ai-powered-sas-migration

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell / cmd)
source .venv/bin/activate       # macOS / Linux (Git Bash on Windows: source .venv/Scripts/activate)

# 3. Install dependencies and this project's package (editable)
pip install -r requirements.txt
pip install -e .

# 4. Add your Anthropic API key (needed for the LLM steps only)
cp .env.example .env            # then edit .env

# 5. Verify: imports work and all SAS data loads
python -m pytest
```

**Data:** everything is already in the repo under `data/Project_1/`: the SAS programs, the input dataset (`Starrating/alldata_2025jul`) and the SAS-produced outputs (`SAS Output/*.sas7bdat` plus CSV copies). Load them with `sas_migration.data_io`:

```python
from sas_migration import data_io
df = data_io.load_input()                          # 4566 hospitals x 93 columns
star = data_io.load_sas_output("star_2025jul")     # SAS ground truth
```

Use the `.sas7bdat` outputs as ground truth for validation. The CSV exports are rounded, with differences of up to about 5e-7.

**Windows note:** `requirements.txt` sets upper version limits on pandas, numpy, scipy and scikit-learn. On machines with Smart App Control, the newest releases' compiled DLLs were blocked on import ("An Application Control policy has blocked this file"). Don't raise the limits without checking that the imports still work.

---

## 🏗️ **Project Overview**

**Describe:**

- How this project is connected to the Break Through Tech AI Program
- Your AI Studio host company and the project objective and scope
- The real-world significance of the problem and the potential impact of your work

---

## 📊 **Data Exploration**

**You might consider describing the following (as applicable):**

* The dataset(s) used: origin, format, size, type of data
* Data exploration and preprocessing approaches
* Insights from your Exploratory Data Analysis (EDA)
* Challenges and assumptions when working with the dataset(s)

**Potential visualizations to include:**

* Plots, charts, heatmaps, feature visualizations, sample dataset images

---

## 🧠 **Model Development**

**You might consider describing the following (as applicable):**

* Model(s) used (e.g., CNN with transfer learning, regression models)
* Feature selection and Hyperparameter tuning strategies
* Training setup (e.g., % of data for training/validation, evaluation metric, baseline performance)


---

## 📈 **Results & Key Findings**

**You might consider describing the following (as applicable):**

* Performance metrics (e.g., Accuracy, F1 score, RMSE)
* How your model performed
* Insights from evaluating model fairness

**Potential visualizations to include:**

* Confusion matrix, precision-recall curve, feature importance plot, prediction distribution, outputs from fairness or explainability tools

---

## 🚀 **Next Steps**

**You might consider addressing the following (as applicable):**

* What are some of the limitations of your model?
* What would you do differently with more time/resources?
* What additional datasets or techniques would you explore?

---

## 📝 **License**

Specify how your project can be used by others. Choose an appropriate license and link it here (e.g., MIT, Apache 2.0). Make sure your Challenge Advisor approves of the selected license type. 

**Example:**
This project is licensed under the MIT License.

---

## 📄 **References** (Optional but encouraged)

Cite relevant papers, articles, or resources that supported your project.

---

## 🙏 **Acknowledgements** (Optional but encouraged)

Thank your Challenge Advisor, host company representatives, TA, and others who supported your project.
