"""DfT / DVLA 来源适配器：原表独立保存，展示时才映射指标代码。

数据库保留 Number / Thousands 和公布文本。TOTAL 的隐藏缓存不冒充精确辆数。
本模块读取已核实的 ODS；无需联网或安装电子表格解析依赖。
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pandas as pd
from evmi import data

SOURCE = 'DFT_DVLA'
SCOPE = 'uk_first_registration_cars_including_imports'
SCOPE_NOTE = '英国 DfT 首次注册 Cars，含进口二手车；与 Eurostat 新乘用车口径不同'
LICENSE_URL = 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/'
ATTRIBUTION = 'Contains public sector information licensed under the Open Government Licence v3.0.'
SOURCE_PAGE = 'https://www.gov.uk/government/statistical-data-sets/vehicle-licensing-statistics-data-tables'
RESEARCH = data.ROOT / 'outputs/phase4/dft_research/20261013T175140_198460Z'
MAPPING = {'Battery electric': 'ELC', 'Plug-in hybrid electric - petrol': 'ELC_PET_PI',
           'Plug-in hybrid electric - diesel': 'ELC_DIE_PI', 'Range extended electric': 'REEV',
           'Cars total': 'TOTAL'}
NAMES = {'ELC': '纯电动乘用车', 'ELC_PET_PI': '汽油插电式混合动力乘用车',
         'ELC_DIE_PI': '柴油插电式混合动力乘用车', 'REEV': '增程式（单列，不计入 PHEV 合计）',
         'TOTAL': '首次注册 Cars 总量（含进口二手车）', 'PHEV_TOTAL': 'PHEV 合计（计算，不含增程式）'}
NS = {k: f'urn:oasis:names:tc:opendocument:xmlns:{k}:1.0' for k in ('office', 'table', 'text')}

def q(kind, name):
    return '{' + NS[kind] + '}' + name

def ods_rows(path, sheet, wanted):
    """只展开指定行的前 15 列，避免把 ODS 的大量空白重复列写成巨型缓存。"""
    found = {}
    with ZipFile(path) as archive, archive.open('content.xml') as handle:
        current, number = None, 1
        for event, element in ET.iterparse(handle, events=('start', 'end')):
            if event == 'start' and element.tag == q('table', 'table'):
                current, number = element.get(q('table', 'name')), 1
            if event == 'end' and element.tag == q('table', 'table-row'):
                repeat = int(element.get(q('table', 'number-rows-repeated'), '1'))
                targets = [r for r in wanted if number <= r < number + repeat] if current == sheet else []
                if targets:
                    cells, col = {}, 1
                    for cell in element:
                        if cell.tag not in (q('table', 'table-cell'), q('table', 'covered-table-cell')):
                            continue
                        count = int(cell.get(q('table', 'number-columns-repeated'), '1'))
                        text = '\n'.join(''.join(p.itertext()) for p in cell.findall('text:p', NS))
                        for c in range(col, min(col + count, 16)):
                            cells[c] = {'display': text, 'raw': cell.get(q('office', 'value'))}
                        col += count
                        if col > 15:
                            break
                    for target in targets:
                        found[target] = cells
                number += repeat
                element.clear()
    if set(found) != set(wanted):
        raise ValueError('ODS 缺少声明的来源行')
    return found

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def reviewed_snapshot():
    """重新核对原始 ODS 单元格；研究报告是索引，不替代原始来源。"""
    findings_path = RESEARCH / 'reviewed_findings_final.json'
    findings = json.loads(findings_path.read_text(encoding='utf-8'))
    manifest = json.loads((RESEARCH / 'download_manifest.json').read_text(encoding='utf-8'))
    attachments = {a['name']: a for a in manifest['attachments']}
    for attachment in attachments.values():
        path = data._project_path(attachment['file'])
        if (attachment['http_status'] != 200 or not attachment['request_url'].startswith(
                'https://assets.publishing.service.gov.uk/media/') or
                sha256(path.read_bytes()).hexdigest() != attachment['sha256']):
            raise ValueError('DfT 原始附件身份或摘要不一致')
    annual = [r for r in findings['annual_findings'] if r['geography'] == 'United Kingdom']
    if sorted(r['year'] for r in annual) != list(range(2022, 2026)):
        raise ValueError('英国四个完整年度未通过核查')
    evidence = []
    for row in annual:
        if row['body_type'] != 'Cars' or row['period'] != 'Annual':
            raise ValueError('统计对象或频率不一致')
        evidence.extend((row['year'], c) for c in row['preferred_fuel_cells'].values())
        evidence.append((row['year'], row['total_cell_evidence']))
    cells_by_sheet = {}
    for _, cell in evidence:
        key = (cell['workbook'], cell['sheet'])
        cells_by_sheet.setdefault(key, {5}).add(int(re.search(r'\d+', cell['cell'])[0]))
    originals = {key: ods_rows(data._project_path(attachments[key[0]]['file']), key[1], rows)
                 for key, rows in cells_by_sheet.items()}
    observations = []
    for year, cell in evidence:
        column, row = re.fullmatch(r'([A-Z]+)(\d+)', cell['cell']).groups()
        column_index = 0
        for c in column:
            column_index = column_index * 26 + ord(c) - 64
        original_row = originals[(cell['workbook'], cell['sheet'])][int(row)]
        header = originals[(cell['workbook'], cell['sheet'])][5][column_index]['display']
        if header != ('Cars' if cell['workbook'] == 'veh0150' else cell['fuel_label']):
            raise ValueError('官方动力/车身列标题变化，必须复核')
        actual = original_row[column_index]
        labels = [v['display'] for v in original_row.values()]
        if not all(any(token in label for label in labels) for token in ('United Kingdom', str(year), 'Annual')):
            raise ValueError('ODS 行不是指定英国年度')
        if cell['workbook'] == 'veh0181' and not all(token in labels for token in ('Cars', 'Number')):
            raise ValueError('动力行的车辆类别或单位不一致')
        if cell['workbook'] == 'veh0150' and 'Thousands' not in labels:
            raise ValueError('TOTAL 原始单位不是公布千辆')
        if actual != {'display': cell['display_text'], 'raw': cell['raw_value']}:
            raise ValueError('研究索引与官方 ODS 单元格不一致')
        source = attachments[cell['workbook']]
        if cell['source_sha256'] != source['sha256'] or cell['source_url'] != source['request_url']:
            raise ValueError('来源单元格引用不一致')
        code = MAPPING[cell['fuel_label']]
        published = Decimal(cell['display_text'].replace(',', ''))
        if published < 0 or (code != 'TOTAL' and published != Decimal(cell['raw_value'])):
            raise ValueError('官方数量无效')
        observations.append({'geo': 'GB', 'original_geography': 'United Kingdom', 'year': year,
            'freq': 'A', 'vehicle_class': 'Cars', 'scope_id': SCOPE, 'original_code': cell['fuel_label'],
            'normalized_code': code, 'source_unit': cell['original_unit'],
            'published_value': str(published), 'published_text': cell['display_text'],
            'raw_value': cell['raw_value'], 'display_decimals': 1 if code == 'TOTAL' else 0,
            'is_missing': 0, 'quality_flags': canonical(cell['flags']),
            'source_sheet': cell['sheet'], 'source_cell': cell['cell'], 'source_url': cell['source_url'],
            'source_file': cell['source_file'], 'source_sha256': cell['source_sha256'],
            'retrieved_at_utc': source['retrieved_at_utc'], 'source_updated': findings['publication_header_date']})
    metadata = {'source_id': SOURCE, 'scope_id': SCOPE, 'source_page': SOURCE_PAGE,
        'source_updated': findings['publication_header_date'],
        'retrieved_at_utc': max(a['retrieved_at_utc'] for a in attachments.values()),
        'license': 'OGL-3.0', 'license_url': LICENSE_URL, 'attribution': ATTRIBUTION,
        'license_review': 'docs/uk_dft_license_review.md', 'scope_note': SCOPE_NOTE,
        'attachments': list(attachments.values()), 'findings_file': str(findings_path.relative_to(data.ROOT)),
        'findings_sha256': sha256(findings_path.read_bytes()).hexdigest(),
        'record_count': len(observations), 'aggregation_rule': 'PHEV_TOTAL = petrol + diesel; excludes REEV',
        'comparability': {m: {'approved_group': None, 'reason': SCOPE_NOTE} for m in
                          ('value', 'yoy_pct', 'delta', 'bev_share_pct', 'structure', 'total')}}
    observations.sort(key=lambda r: (r['year'], r['normalized_code']))
    return metadata, observations

SCHEMA = '''
CREATE TABLE IF NOT EXISTS external_source_batches (
 batch_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, scope_id TEXT NOT NULL,
 retrieved_at_utc TEXT NOT NULL, imported_at_utc TEXT NOT NULL,
 manifest_file TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, record_count INTEGER NOT NULL CHECK(record_count>0)
) STRICT;
CREATE TABLE IF NOT EXISTS external_observations (
 batch_id TEXT NOT NULL REFERENCES external_source_batches(batch_id),
 geo TEXT NOT NULL, original_geography TEXT NOT NULL, year INTEGER NOT NULL,
 freq TEXT NOT NULL, vehicle_class TEXT NOT NULL, scope_id TEXT NOT NULL,
 original_code TEXT NOT NULL, normalized_code TEXT NOT NULL, source_unit TEXT NOT NULL,
 published_value TEXT NOT NULL, published_text TEXT NOT NULL, raw_value TEXT NOT NULL,
 display_decimals INTEGER NOT NULL, is_missing INTEGER NOT NULL CHECK(is_missing IN (0,1)),
 quality_flags TEXT NOT NULL, source_sheet TEXT NOT NULL, source_cell TEXT NOT NULL,
 source_url TEXT NOT NULL, source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 retrieved_at_utc TEXT NOT NULL, source_updated TEXT NOT NULL,
 PRIMARY KEY(batch_id,geo,year,normalized_code)
) STRICT;
CREATE VIEW IF NOT EXISTS external_phev_totals AS
 SELECT p.batch_id,p.geo,p.year,CAST(p.published_value AS INTEGER)+CAST(d.published_value AS INTEGER) AS value,
        p.source_cell AS petrol_cell,d.source_cell AS diesel_cell,'PHEV_TOTAL' AS normalized_code,
        'calculated_petrol_plus_diesel_excludes_reev' AS calculation
 FROM external_observations p JOIN external_observations d
 ON p.batch_id=d.batch_id AND p.geo=d.geo AND p.year=d.year AND p.scope_id=d.scope_id
 AND p.source_unit=d.source_unit AND p.freq=d.freq AND p.vehicle_class=d.vehicle_class
 WHERE p.normalized_code='ELC_PET_PI' AND d.normalized_code='ELC_DIE_PI'
 AND p.source_unit='Number' AND p.is_missing=0 AND d.is_missing=0;
'''

def initialize(connection):
    # 新表与旧表没有写操作；DDL 与入库由调用者同一事务管理。
    for statement in SCHEMA.split(';'):
        if statement.strip():
            connection.execute(statement)

def validate_rows(metadata, rows):
    expected = {(year, code) for year in range(2022, 2026) for code in MAPPING.values()}
    if len(rows) != 20 or {(r['year'], r['normalized_code']) for r in rows} != expected:
        raise ValueError('英国完整批次需要四年 × 五项原始观察，PHEV 合计不能重复入原始表')
    if metadata['source_id'] != SOURCE or metadata['scope_id'] != SCOPE or metadata['license'] != 'OGL-3.0':
        raise ValueError('来源、许可或范围合同不匹配')
    for r in rows:
        unit = 'Thousands' if r['normalized_code'] == 'TOTAL' else 'Number'
        if (r['geo'] != 'GB' or r['original_geography'] != 'United Kingdom' or r['scope_id'] != SCOPE
                or r['source_unit'] != unit or r['freq'] != 'A' or r['vehicle_class'] != 'Cars'
                or MAPPING.get(r['original_code']) != r['normalized_code'] or r['is_missing'] != 0
                or Decimal(r['published_value']) < 0):
            raise ValueError('英国记录不满足来源合同')

def store_snapshot(connection, batch, metadata, rows):
    """调用方开启事务；重复批次逐字段一致才返回 already_imported。"""
    validate_rows(metadata, rows)
    initialize(connection)
    existing = connection.execute('SELECT * FROM external_source_batches WHERE batch_id=?', (batch['batch_id'],)).fetchone()
    columns = list(batch)
    if existing:
        existing_batch = dict(zip(columns, connection.execute('SELECT '+','.join(columns)+
                                  ' FROM external_source_batches WHERE batch_id=?', (batch['batch_id'],)).fetchone()))
        # imported_at 是第一次入库时间，重复校验不要求当前执行时间相等。
        if any(existing_batch[k] != batch[k] for k in columns if k != 'imported_at_utc'):
            raise ValueError('重复批次元数据冲突，不覆盖')
        keys = list(rows[0])
        saved = connection.execute('SELECT '+','.join(keys)+' FROM external_observations WHERE batch_id=? ORDER BY year,normalized_code', (batch['batch_id'],)).fetchall()
        if [tuple(r[k] for k in keys) for r in rows] != [tuple(r) for r in saved]:
            raise ValueError('重复批次记录冲突，不覆盖')
        return 'already_imported'
    connection.execute('INSERT INTO external_source_batches ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', tuple(batch.values()))
    keys = ['batch_id', *rows[0]]
    connection.executemany('INSERT INTO external_observations ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',
                           [(batch['batch_id'], *r.values()) for r in rows])
    return 'imported'

def import_reviewed(db_path=data.DEFAULT_DB):
    metadata, rows = reviewed_snapshot()
    validate_rows(metadata, rows)
    # 内容身份稳定：同一官方附件重复执行不会新增一个伪采集批次。
    identity = sha256(canonical({'metadata': metadata, 'rows': rows}).encode()).hexdigest()[:16]
    batch_id = 'DFT_' + metadata['retrieved_at_utc'][:10].replace('-', '') + '_' + identity
    folder = data.ROOT / 'outputs/phase4/uk_batches' / batch_id
    path = folder / 'manifest.json'
    payload = {'batch_id': batch_id, 'metadata': metadata, 'observations': rows}
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != payload:
            raise ValueError('已有清单不一致，不覆盖')
    else:
        folder.mkdir(parents=True, exist_ok=False)
        path.write_text(canonical(payload), encoding='utf-8')
    batch = {'batch_id': batch_id, 'source_id': SOURCE, 'scope_id': SCOPE,
        'retrieved_at_utc': metadata['retrieved_at_utc'], 'imported_at_utc': datetime.now(timezone.utc).isoformat(),
        'manifest_file': path.relative_to(data.ROOT).as_posix(),
        'manifest_sha256': sha256(path.read_bytes()).hexdigest(), 'record_count': len(rows)}
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            result = store_snapshot(connection, batch, metadata, rows)
    return {'batch_id': batch_id, 'result': result, 'official_rows': len(rows), 'derived_phev_rows': 4}

def list_batches(db_path=data.DEFAULT_DB):
    with closing(data.open_readonly(db_path)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='external_source_batches'").fetchone():
            return []
        return [dict(r) for r in connection.execute('SELECT * FROM external_source_batches WHERE source_id=? ORDER BY retrieved_at_utc DESC,batch_id', (SOURCE,))]

def load_batch(batch_id, db_path=data.DEFAULT_DB):
    """展示只从 SQLite 读取数值；ODS 摘要、清单和数据库全字段互校。"""
    with closing(data.open_readonly(db_path)) as connection:
        batch = connection.execute('SELECT * FROM external_source_batches WHERE batch_id=?', (batch_id,)).fetchone()
        if batch is None:
            raise ValueError('找不到英国来源批次')
        batch = dict(batch)
        path = data._project_path(batch['manifest_file'])
        if sha256(path.read_bytes()).hexdigest() != batch['manifest_sha256']:
            raise ValueError('英国来源清单摘要不一致')
        payload = json.loads(path.read_text(encoding='utf-8'))
        meta = payload['metadata']
        rows = [dict(r) for r in connection.execute('SELECT * FROM external_observations WHERE batch_id=? ORDER BY year,normalized_code', (batch_id,))]
    compare = [{k: v for k, v in r.items() if k != 'batch_id'} for r in rows]
    if payload['batch_id'] != batch_id or compare != payload['observations'] or len(rows) != batch['record_count']:
        raise ValueError('英国数据库与来源清单不一致')
    validate_rows(meta, compare)
    for key in ('source_id', 'scope_id', 'retrieved_at_utc', 'record_count'):
        if batch[key] != meta[key]:
            raise ValueError('英国批次来源身份不一致')
    for a in meta['attachments']:
        if sha256(data._project_path(a['file']).read_bytes()).hexdigest() != a['sha256']:
            raise ValueError('英国原始附件摘要不一致')
    return {**batch, **meta}, pd.DataFrame(rows)

def display_frame(metadata, rows):
    """共用图表需要统一列名；原始来源、单位、原代码仍随每一行保留。"""
    output = []
    for r in rows.to_dict('records'):
        code = r['normalized_code']
        # 用公布千辆值 ×1000 仅作显示换算，不用 ODS 隐藏缓存冒充精确辆数。
        value = int(Decimal(r['published_value']) * (1000 if r['source_unit'] == 'Thousands' else 1))
        output.append({**r, 'dataset': 'DfT/DVLA · ' + r['source_sheet'], 'country': '英国',
            'mot_nrg': code, 'mot_nrg_label': r['original_code'], 'powertrain': code,
            'powertrain_zh': NAMES[code], 'unit': 'NR', 'value': value,
            'status': '', 'status_label': '未附加单元格质量标记；历史分类可能修订',
            'source_id': SOURCE, 'scope_note': SCOPE_NOTE, 'cross_country_comparable': False,
            'value_kind': 'rounded_unit_conversion' if code == 'TOTAL' else 'official_observation',
            'license_url': LICENSE_URL, 'attribution': ATTRIBUTION})
    frame = pd.DataFrame(output)
    frame['value'] = pd.array(frame.value, dtype='Int64')
    # PHEV 总数是派生行，有输入引用，不写入原始观察表，选中时替代两个分项。
    for year in sorted(frame.year.unique()):
        pair = frame.loc[frame.year.eq(year) & frame.mot_nrg.isin(['ELC_PET_PI', 'ELC_DIE_PI'])]
        if len(pair) == 2 and not pair.is_missing.any():
            p = pair.iloc[0].to_dict()
            p.update(mot_nrg='PHEV_TOTAL', powertrain='PHEV_TOTAL', powertrain_zh=NAMES['PHEV_TOTAL'],
                mot_nrg_label='Calculated petrol + diesel PHEV; excludes REEV', original_code='DERIVED_PHEV_TOTAL',
                normalized_code='PHEV_TOTAL', value=int(pair.value.sum()), published_value=None, published_text=None,
                raw_value=None, value_kind='calculated', source_cell='+'.join(pair.source_cell),
                formula='PHEV_petrol + PHEV_diesel (REEV excluded)')
            output.append(p)
    frame = pd.DataFrame(output)
    frame['value'] = pd.array(frame.value, dtype='Int64')
    return frame
