indf = datasets['WORK.ALL_DATA_2025JUL']
measure_list = expand_name_list(macro_vars['measure_all'], indf.columns)

res = {}
res['_TYPE_'] = ['0']
res['_PAGE_'] = [1]
res['_TABLE_'] = [1]

for m in measure_list:
    if m in indf.columns:
        res[f'{m}_N'] = [indf[m].notna().sum()]
    else:
        res[f'{m}_N'] = [0]

out_df = pd.DataFrame(res)
outputs = {'WORK.MEASURE_VOLUME0': out_df}