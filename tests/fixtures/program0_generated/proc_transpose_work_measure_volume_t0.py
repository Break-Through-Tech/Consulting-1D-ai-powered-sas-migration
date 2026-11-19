df = datasets['WORK.MEASURE_VOLUME0'].copy()
numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
records = []
for col in numeric_cols:
    records.append({'_NAME_': col, '_LABEL_': '', 'COL1': df[col].iloc[0] if len(df) > 0 else np.nan})
out_df = pd.DataFrame(records)
outputs = {'WORK.MEASURE_VOLUME_T0': out_df}