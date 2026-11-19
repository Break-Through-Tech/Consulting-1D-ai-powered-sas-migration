df = datasets['WORK.SUMMARY_SCORE21'].copy()
df = df[df['report_indicator'] == 1].copy()

outcomes_mortality_cnt = pd.to_numeric(df['Outcomes_Mortality_cnt'], errors='coerce').fillna(0)
outcomes_safety_cnt = pd.to_numeric(df['Outcomes_safety_cnt'], errors='coerce').fillna(0)
outcomes_readmission_cnt = pd.to_numeric(df['Outcomes_Readmission_cnt'], errors='coerce').fillna(0)
patient_experience_cnt = pd.to_numeric(df['Patient_Experience_cnt'], errors='coerce').fillna(0)
process_cnt = pd.to_numeric(df['Process_cnt'], errors='coerce').fillna(0)

df['total_grp'] = ((outcomes_mortality_cnt >= 3).astype(int) +
                  (outcomes_safety_cnt >= 3).astype(int) +
                  (outcomes_readmission_cnt >= 3).astype(int) +
                  (patient_experience_cnt >= 3).astype(int) +
                  (process_cnt >= 3).astype(int))

total_measure_group_cnt = pd.to_numeric(df['Total_measure_group_cnt'], errors='coerce')

df['cnt_grp'] = np.select(
    [
        total_measure_group_cnt == 3,
        total_measure_group_cnt == 4,
        total_measure_group_cnt == 5
    ],
    [
        '1) # of groups=3       ',
        '2) # of groups=4       ',
        '3) # of groups=5       '
    ],
    default=None
)

outputs = {'WORK.SUMMARY_SCORE22': df}