df1 = datasets['WORK.SUMMARY_SCORE21'].copy()
df2 = datasets['WORK.CNT_S_STR'].copy()
if 'PROVIDER_ID' in df1.columns:
    key1 = 'PROVIDER_ID'
else:
    key1 = 'provider_ID'
if 'PROVIDER_ID' in df2.columns:
    key2 = 'PROVIDER_ID'
else:
    key2 = 'provider_ID'
df1 = df1.sort_values(by=key1)
df2 = df2.sort_values(by=key2)
merged = pd.merge(df1, df2, left_on=key1, right_on=key2, how='outer')
if key2 != key1 and key2 in merged.columns:
    merged = merged.drop(columns=[key2])
if key1 != 'PROVIDER_ID' and key1 in merged.columns:
    merged = merged.rename(columns={key1: 'PROVIDER_ID'})
outputs = {'STAR_2025JUL': merged}