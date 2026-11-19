def keep_hos(indsn, varlist, nmeasure):
    df = indsn.copy()
    n = int(nmeasure)
    expanded_vars = expand_name_list(varlist, df.columns)
    
    c_cols = [f'c{i}' for i in range(1, n + 1)]
    
    for c, m in zip(c_cols, expanded_vars):
        s = df[m]
        df[c] = np.where(s.notna() & (s != ''), 1, 0)
        
    df['Total_m_cnt'] = df[c_cols].sum(axis=1)
    
    df = df[df['Total_m_cnt'] >= 1].copy()
    
    drop_cols = c_cols + ['k']
    df = df.drop(columns=[col for col in drop_cols if col in df.columns])
    
    return df