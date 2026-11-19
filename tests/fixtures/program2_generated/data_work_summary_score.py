df = datasets['WORK.ALL'].copy()

df['std_weight_PatientExperience'] = 0.22
df['std_weight_Readmission'] = 0.22
df['std_weight_Mortality'] = 0.22
df['std_weight_safety'] = 0.22
df['std_weight_Process'] = 0.12

std_weights = [
    df['std_weight_PatientExperience'],
    df['std_weight_Readmission'],
    df['std_weight_Mortality'],
    df['std_weight_safety'],
    df['std_weight_Process']
]

scores = [
    df.get('Std_PatientExp_score', pd.Series([np.nan]*len(df))),
    df.get('Std_Outcomes_Readmission_score', pd.Series([np.nan]*len(df))),
    df.get('Std_Outcomes_Mortality_score', pd.Series([np.nan]*len(df))),
    df.get('Std_Outcomes_Safety_score', pd.Series([np.nan]*len(df))),
    df.get('Std_Process_score', pd.Series([np.nan]*len(df)))
]

indicators = []
for i in range(5):
    ind = scores[i].isna().astype(int)
    indicators.append(ind)

denom_terms = [indicators[i] * std_weights[i] for i in range(5)]
denom = 1.0 - sum(denom_terms)

weights = []
sum_weight_aves = []
for i in range(5):
    w = np.where(scores[i].isna(), np.nan, std_weights[i] / denom)
    weights.append(w)
    swa = np.where(pd.isna(w), 0, w * scores[i])
    sum_weight_aves.append(swa)

df['weight_PatientExperience'] = weights[0]
df['weight_Outcomes_Readmission'] = weights[1]
df['weight_Outcomes_Mortality'] = weights[2]
df['weight_Outcomes_Safety'] = weights[3]
df['weight_Process'] = weights[4]

swa_df = pd.DataFrame(sum_weight_aves).T
df['summary_score'] = swa_df.sum(axis=1, skipna=True)

outputs = {'WORK.SUMMARY_SCORE': df}