def kmeans(in_ds, out_ds, grp):
    summary_score2 = in_ds.copy()
    
    pctlpts = list(range(20, 101, 20))
    s1 = sas_procs.univariate_percentiles(summary_score2, 'summary_score', pctlpts, pctlpre='P')
    
    s2 = summary_score2.copy()
    for col in s1.columns:
        s2[col] = s1.iloc[0][col]
        
    conditions = [
        (s2['summary_score'].notna()) & (s2['summary_score'] <= s2['P20']),
        (s2['P20'] < s2['summary_score']) & (s2['summary_score'] <= s2['P40']),
        (s2['P40'] < s2['summary_score']) & (s2['summary_score'] <= s2['P60']),
        (s2['P60'] < s2['summary_score']) & (s2['summary_score'] <= s2['P80']),
        (s2['P80'] < s2['summary_score'])
    ]
    choices = [1, 2, 3, 4, 5]
    s2['grp'] = np.select(conditions, choices, default=np.nan)
    
    s2_keep_cols = ['PROVIDER_ID', 'summary_score', 'grp', 'P20', 'P40', 'P60', 'P80']
    s2 = s2[[c for c in s2_keep_cols if c in s2.columns]]
    
    grouped = s2.groupby('grp')['summary_score'].median().reset_index()
    s33 = grouped.rename(columns={'summary_score': 'summary_score_median'})
    s33['summary_score'] = s33['summary_score_median']
    
    fc1 = sas_procs.fastclus(summary_score2, 'summary_score', s33, maxc=5, maxiter=1000, converge=0)
    seeds2 = fc1.cluster_centers
    
    fc2 = sas_procs.fastclus(summary_score2, 'summary_score', seeds2, maxc=5, maxiter=1000, converge=0, strict=1)
    cluster_mean = fc2.cluster_centers
    clusters = fc2.out
    
    cluster_sort = cluster_mean.rename(columns={'summary_score': 'mean_summary_score_star'}).sort_values('mean_summary_score_star').copy()
    cluster_sort['star'] = range(1, len(cluster_sort) + 1)
    cluster_sort['cluster_2'] = cluster_sort['Cluster'].astype(float)
    
    cluster_sort = cluster_sort.rename(columns={'cluster_2': 'CLUSTER'})
    
    clusters2 = clusters.copy()
    clusters2['CLUSTER'] = clusters2['CLUSTER'].abs()
    
    clusters2 = clusters2.sort_values('CLUSTER')
    cluster_sort = cluster_sort.sort_values('CLUSTER')
    
    if 'summary_score' in cluster_sort.columns and 'summary_score' in clusters2.columns:
        cluster_sort = cluster_sort.drop(columns=['summary_score'])
    
    results = pd.merge(clusters2, cluster_sort, on='CLUSTER', how='inner')
    
    drop_cols = [c for c in ['CLUSTER', 'DISTANCE', 'mean_summary_score_star'] if c in results.columns]
    results = results.drop(columns=drop_cols)
    
    results = results.sort_values('PROVIDER_ID')
    
    final_cols = ['PROVIDER_ID']
    if grp and grp not in final_cols:
        final_cols.append(grp)
    if 'star' not in final_cols:
        final_cols.append('star')
        
    out_df = results[[c for c in final_cols if c in results.columns]].copy()
    if grp and grp != 'grp' and 'grp' in out_df.columns:
        out_df = out_df.rename(columns={'grp': grp})
        
    return out_df