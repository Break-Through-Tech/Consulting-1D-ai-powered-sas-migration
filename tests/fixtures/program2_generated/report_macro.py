def report(indsn, outdsn, nmeasure_OM, nmeasure_OS, nmeasure_OR, nmeasure_PtExp, nmeasure_Process, measure_OM, measure_OS, measure_OR, measure_PtExp, measure_Process):
    df = indsn.copy()
    
    n_om = int(nmeasure_OM)
    n_os = int(nmeasure_OS)
    n_or = int(nmeasure_OR)
    n_pte = int(nmeasure_PtExp)
    n_proc = int(nmeasure_Process)
    
    cols_om = expand_name_list(measure_OM, df.columns)
    cols_os = expand_name_list(measure_OS, df.columns)
    cols_or = expand_name_list(measure_OR, df.columns)
    cols_pte = expand_name_list(measure_PtExp, df.columns)
    cols_proc = expand_name_list(measure_Process, df.columns)
    
    m_df = pd.DataFrame(index=df.index)
    for i in range(1, n_om + 1):
        col_name = cols_om[i-1]
        m_df[f'm_{i}'] = np.where(df[col_name].notna(), 1, 0)
    outcomes_mortality_cnt = m_df[[f'm_{i}' for i in range(1, n_om + 1)]].sum(axis=1)
    
    s_df = pd.DataFrame(index=df.index)
    for i in range(1, n_os + 1):
        col_name = cols_os[i-1]
        s_df[f's_{i}'] = np.where(df[col_name].notna(), 1, 0)
    outcomes_safety_cnt = s_df[[f's_{i}' for i in range(1, n_os + 1)]].sum(axis=1)
    
    r_df = pd.DataFrame(index=df.index)
    for i in range(1, n_or + 1):
        col_name = cols_or[i-1]
        r_df[f'r_{i}'] = np.where(df[col_name].notna(), 1, 0)
    outcomes_readmission_cnt = r_df[[f'r_{i}' for i in range(1, n_or + 1)]].sum(axis=1)
    
    h_df = pd.DataFrame(index=df.index)
    for i in range(1, n_pte + 1):
        col_name = cols_pte[i-1]
        h_df[f'h_{i}'] = np.where(df[col_name].notna(), 1, 0)
    patient_experience_cnt = h_df[[f'h_{i}' for i in range(1, n_pte + 1)]].sum(axis=1)
    
    pc_df = pd.DataFrame(index=df.index)
    for i in range(1, n_proc + 1):
        col_name = cols_proc[i-1]
        pc_df[f'pc_{i}'] = np.where(df[col_name].notna(), 1, 0)
    process_cnt = pc_df[[f'pc_{i}' for i in range(1, n_proc + 1)]].sum(axis=1)
    
    d1 = np.where(patient_experience_cnt >= 3, 1, 0)
    d2 = np.where(outcomes_mortality_cnt >= 3, 1, 0)
    d3 = np.where(outcomes_readmission_cnt >= 3, 1, 0)
    d4 = np.where(outcomes_safety_cnt >= 3, 1, 0)
    d5 = np.where(process_cnt >= 3, 1, 0)
    
    total_measure_group_cnt = d1 + d2 + d3 + d4 + d5
    mortsafe_group_cnt = np.where(outcomes_mortality_cnt >= 3, 1, 0) + np.where(outcomes_safety_cnt >= 3, 1, 0)
    report_indicator = np.where((mortsafe_group_cnt >= 1) & (total_measure_group_cnt >= 3), 1, 0)
    
    res = pd.DataFrame({
        'PROVIDER_ID': df['PROVIDER_ID'],
        'Outcomes_Mortality_cnt': outcomes_mortality_cnt,
        'Outcomes_safety_cnt': outcomes_safety_cnt,
        'Outcomes_Readmission_cnt': outcomes_readmission_cnt,
        'Patient_Experience_cnt': patient_experience_cnt,
        'Process_cnt': process_cnt,
        'Total_measure_group_cnt': total_measure_group_cnt,
        'MortSafe_Group_cnt': mortsafe_group_cnt,
        'report_indicator': report_indicator
    })
    
    return res