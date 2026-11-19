df = datasets['WORK.INCLUDE_MEASURE']
count = int(df['measure_in_name'].dropna().count())
outputs = {}
new_macro_vars = {'measure_cnt': str(count)}