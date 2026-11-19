indf = datasets['WORK.ALL_DATA_2025JUL']
measure_list_str = macro_vars['measure_all']
measure_cols = expand_name_list(measure_list_str, indf.columns)

counts = {}
for col in measure_cols:
    counts[f"{col}_N"] = int(indf[col].notna().sum())

out_row = {
    '_TYPE_': '0',
    '_PAGE_': 1,
    '_TABLE_': 1,
}
out_row.update(counts)

outputs = {'WORK.MEASURE_VOLUME': pd.DataFrame([out_row])}