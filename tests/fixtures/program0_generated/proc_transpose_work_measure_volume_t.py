df = datasets['WORK.MEASURE_VOLUME'].copy()
rows = []
for col in df.columns:
    if col.upper() == '_TYPE_':
        continue
    val = df[col].iloc[0]
    rows.append({'_NAME_': col, '_LABEL_': '', 'COL1': val})
out_df = pd.DataFrame(rows)
outputs = {'WORK.MEASURE_VOLUME_T': out_df}