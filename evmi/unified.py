"""统一展示与严格计算的边界。展示连接不等于求和，也不修改原始 Eurostat 表。"""
from html import escape
import json
import pandas as pd
from evmi import analytics, dft

OVERVIEW = '11国市场概览'
STRICT = '严格可比分析'
MODES = (OVERVIEW, STRICT)
NOTICE = '共同展示不代表同口径：英国 DfT/DVLA 含进口二手车；Eurostat 为新乘用车。混合来源不合计、不生成严格排名。'
EXTRA_NAMES = {'PHEV_TOTAL': 'PHEV 合计（替代汽油/柴油分项）', 'TOTAL': '全部首次注册 Cars（英国公布千辆）'}
DISPLAY_CODES = (*analytics.ENERGIES, 'PHEV_TOTAL', 'TOTAL')

def eurostat_frame(raw):
    frame = raw.copy()
    frame['source_id'] = 'EUROSTAT'
    frame['scope_id'] = 'new_passenger_registrations'
    frame['scope_note'] = 'Eurostat 年度新乘用车首次注册；不含进口二手车'
    frame['source_unit'] = frame.unit
    frame['value_kind'] = 'official_observation'
    frame['cross_country_comparable'] = True
    return frame


def extra_series(raw, totals):
    """完整两分项才计算 PHEV 合计；TOTAL 始终独立，不能与动力相加。"""
    derived = []
    for (_, geo, year), group in raw.groupby(['batch_id', 'geo', 'year']):
        pair = group.loc[group.mot_nrg.isin(['ELC_PET_PI', 'ELC_DIE_PI'])]
        if len(pair) == 2:
            row = pair.iloc[0].to_dict()
            missing = bool(pair.is_missing.any() or pair.value.isna().any())
            row.update(mot_nrg='PHEV_TOTAL', mot_nrg_label='Calculated petrol + diesel PHEV',
                powertrain='PHEV_TOTAL', powertrain_zh='PHEV 合计（计算，不含增程式）',
                value=None if missing else int(pair.value.sum()), is_missing=int(missing), value_kind='calculated',
                status=''.join(sorted(set(''.join(pair.status.fillna(''))))),
                formula='ELC_PET_PI + ELC_DIE_PI; excludes REEV')
            if row['source_id'] == dft.SOURCE:
                row.update(published_value=None, published_text=None, raw_value=None,
                           source_cell='+'.join(pair.source_cell), original_code='DERIVED_PHEV_TOTAL')
            row['input_refs'] = json.dumps([f"{r.batch_id}/{r.geo}/{r.year}/{r.mot_nrg}" for r in pair.itertuples()])
            derived.append(row)
    return pd.concat([raw, totals, pd.DataFrame(derived)], ignore_index=True)

def allowed(source_id, metric):
    """按指标单独审批；未来开放英国某项须在此登记有依据的可比合同。"""
    approved = {'EUROSTAT': frozenset({'value', 'yoy_pct', 'delta', 'bev_share_pct', 'structure', 'total'})}
    return metric in approved.get(source_id, frozenset())

def strict_sample(frame, metric):
    return frame.loc[frame.source_id.map(lambda source: allowed(source, metric))].copy()

def selection_codes(energies):
    """TOTAL 包含所有动力；PHEV 合计包含两个分项，防止重复计数。"""
    if 'TOTAL' in energies:
        return ['TOTAL']
    if 'PHEV_TOTAL' in energies:
        return [c for c in energies if c not in ('ELC_PET_PI', 'ELC_DIE_PI')]
    return list(energies)

def per_scope(frame):
    # 原分析层的一批次一口径保护继续生效；每组分别计算后仅连接展示行。
    return frame.groupby(['batch_id', 'dataset', 'freq', 'unit'], sort=False, dropna=False)

def national_aggregate(prepared, countries, energies, year):
    subset = prepared.loc[prepared.geo.isin(countries) & prepared.mot_nrg.isin(selection_codes(energies))]
    # 单国的多张同口径 DfT 表可以使用共同展示身份，但不改变源数据 dataset 字段。
    if subset.empty:
        return analytics.aggregate_year(subset, countries, selection_codes(energies), year)
    if subset.source_id.nunique() != 1 or subset.batch_id.nunique() != 1 or subset.scope_id.nunique() != 1:
        raise ValueError('禁止跨来源或跨批次合计')
    local = subset.copy()
    if local.source_id.eq(dft.SOURCE).all():
        if len(countries) != 1:
            raise ValueError('DfT 当前只批准英国单国分析')
        local['dataset'] = 'DFT_DVLA_same_scope_display'
    return analytics.aggregate_year(local, countries, selection_codes(energies), year)

def national_share(prepared, totals, country, year, exclude=False):
    national = prepared.loc[prepared.geo.eq(country)]
    if country != 'GB':
        return analytics.market_share(national, totals.loc[totals.geo.eq(country)], [country], year, exclude_imputed=exclude)
    bev = national.loc[national.year.eq(year) & national.mot_nrg.eq('ELC')]
    total = totals.loc[totals.geo.eq(country) & totals.year.eq(year)]
    result = {'value': None, 'complete': False, 'reason': 'total_missing', 'available_count': 0, 'expected_count': 1}
    if len(bev) != 1 or len(total) != 1:
        return result
    n, den = bev.iloc[0], total.iloc[0]
    if not n.eligible:
        result['reason'] = 'current_excluded'
    elif not all(n[k] == den[k] for k in ('batch_id', 'geo', 'year', 'freq', 'scope_id', 'source_id')):
        result['reason'] = 'incomparable_scope'
    elif den.is_missing or pd.isna(den.value):
        pass
    elif den.value == 0:
        result['reason'] = 'total_zero'
    elif n.value > den.value:
        result['reason'] = 'numerator_gt_total'
    else:
        result.update(value=float(n.value) / float(den.value) * 100, complete=True,
                      reason='rounded_dft_total', available_count=1)
    return result

def structures(prepared, countries):
    frames = []
    for _, group in per_scope(prepared.loc[prepared.mot_nrg.isin(analytics.ENERGIES)]):
        current = [c for c in countries if c in set(group.geo)]
        if current:
            result = analytics.structure_by_year(group, current)
            result['dataset'] = group.dataset.iloc[0]
            result['scope_note'] = group.scope_note.iloc[0]
            frames.append(result)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def coverage(prepared, countries, years):
    results = []
    for country in countries:
        national = prepared.loc[prepared.geo.eq(country) & prepared.mot_nrg.isin(analytics.ENERGIES)]
        result = analytics.coverage_by_country(national, [country], years)
        result['source_id'] = national.source_id.iloc[0] if not national.empty else '暂无数据'
        result['strict_comparable'] = allowed(result.source_id.iloc[0], 'value')
        results.append(result)
    return pd.concat(results, ignore_index=True)

def insights(prepared, countries, energies, year):
    output = []
    for _, group in per_scope(prepared):
        output.extend(analytics.build_insights(group, countries, selection_codes(energies), year))
    # 概览只按国家、动力排序，不把混合来源的净增大小包装为跨国排名。
    return sorted(output, key=lambda row: (row['geo'], row['mot_nrg']))

def export_csv(frame):
    # 所有额外来源字段随下载保留，而不是只导出原 Eurostat 18 列。
    return frame.to_csv(index=False).encode('utf-8-sig')

def overview_html(prepared, countries, energies, year):
    selected = analytics.filter_observations(prepared, countries, selection_codes(energies), [year])
    selected = selected[['geo', 'year', 'powertrain_zh', 'value', 'yoy_pct', 'source_id', 'source_unit', 'scope_note']].copy()
    selected.columns = ['国家', '年份', '动力', '展示辆数', '同比%', '来源', '原始单位', '统计范围']
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><title>11国市场概览</title><style>body{font:12px sans-serif;margin:24px}table{border-collapse:collapse;width:100%;font-size:10px}td,th{border:1px solid #aab;padding:5px}tr{break-inside:avoid}@page{size:A4 landscape;margin:12mm}</style><h1>所选国家市场概览 · '+str(year)+'</h1><p>'+escape(NOTICE)+'</p>'+selected.to_html(index=False, escape=True, na_rep='不可计算')+'<p>英国 TOTAL 为公布千辆值换算的约数。PHEV 合计为两分项计算值，不含 REEV。</p><p>'+escape(dft.ATTRIBUTION)+' <a href="'+dft.LICENSE_URL+'">OGL v3.0</a> · <a href="'+dft.SOURCE_PAGE+'">DfT/DVLA 来源</a></p></html>'

def evidence_bundle(metadata, original, prepared):
    """结构化事实与计算引用；没有调用 AI，也不生成政策原因。"""
    facts = json.loads(original.to_json(orient='records'))
    metrics = []
    for row in prepared.to_dict('records'):
        if row['mot_nrg'] == 'REEV':
            continue
        reference = f"{row['batch_id']}/GB/{row['year']}/{row['mot_nrg']}"
        previous = f"{row['batch_id']}/GB/{int(row['year'])-1}/{row['mot_nrg']}"
        metrics.append({'reference': reference, 'value': int(row['value']), 'value_kind': row['value_kind'],
            'year': int(row['year']), 'indicator': row['mot_nrg'], 'source_cell': row['source_cell'],
            'delta': None if pd.isna(row['delta']) else int(row['delta']),
            'yoy_pct': None if pd.isna(row['yoy_pct']) else float(row['yoy_pct']), 'yoy_status': row['yoy_status'],
            'formula': '(current - previous_calendar_year) / previous_calendar_year * 100',
            'inputs': [reference, previous] if row['yoy_status'] == 'ok' else [reference],
            'phev_inputs': [f"{row['batch_id']}/GB/{row['year']}/{c}" for c in ('ELC_PET_PI','ELC_DIE_PI')]
                          if row['mot_nrg']=='PHEV_TOTAL' else [],
            'cross_country_comparable': False})
    return {'schema_version': 'dft-1.0', 'sources': metadata, 'official_facts': facts,
            'calculated_metrics': metrics,
            'bev_total_shares': [{'year': int(year), **national_share(prepared, prepared.loc[prepared.mot_nrg.eq('TOTAL')], 'GB', int(year)),
                'formula':'BEV / (published_TOTAL_thousands * 1000) * 100',
                'inputs':[f"{metadata['batch_id']}/GB/{year}/{code}" for code in ('ELC','TOTAL')],
                'cross_country_comparable':False} for year in sorted(prepared.year.unique())],
            'scope_note': NOTICE, 'unverified_business_hypotheses': []}
