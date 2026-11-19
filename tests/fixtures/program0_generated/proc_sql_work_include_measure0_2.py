df = datasets['WORK.INCLUDE_MEASURE0']
count_val = int(df['measure_in_name'].dropna().count())
outputs = {}
new_macro_vars = {'measure_cnt': str(count_val)}