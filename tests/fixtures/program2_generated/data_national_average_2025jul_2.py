indsn_name = macro_vars.get('NATIONAL_MEAN', 'R.national_average_2025Jul')
if indsn_name.lower().startswith('runtime (') or not indsn_name:
    indsn_name = 'NATIONAL_AVERAGE_2025JUL'

actual_key = next((k for k in datasets.keys() if k.upper() == indsn_name.upper()), 'NATIONAL_AVERAGE_2025JUL')
df = datasets[actual_key].copy()

outputs = {
    'NATIONAL_AVERAGE_2025JUL': df
}