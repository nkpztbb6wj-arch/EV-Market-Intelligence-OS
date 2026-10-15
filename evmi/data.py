"""只读 SQLite 数据层；离线核对采集证据，不请求 Eurostat 网络接口。"""

from contextlib import closing
import csv
from datetime import datetime, timezone
from hashlib import sha256
from itertools import product
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "ev_market_intelligence.sqlite3"
CSV_COLUMNS = (
    "dataset", "geo", "country", "year", "freq", "unit", "mot_nrg", "mot_nrg_label",
    "powertrain", "powertrain_zh", "value", "is_missing", "status", "status_label",
    "source_updated", "retrieved_at_utc", "source_url",
)
ROW_COLUMNS = ("batch_id",) + CSV_COLUMNS
ENERGY_TYPES = {
    "ELC": ("Electricity", "BEV", "纯电动乘用车"),
    "ELC_PET_PI": ("Plug-in hybrid petrol-electric", "PHEV_petrol", "汽油插电式混合动力乘用车"),
    "ELC_DIE_PI": ("Plug-in hybrid diesel-electric", "PHEV_diesel", "柴油插电式混合动力乘用车"),
}
TOTAL_TYPES = {"TOTAL": ("Total", "TOTAL", "全部新乘用车")}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _utc(value):
    """真正解析时间再排序；文字排序可能误判不同 UTC 偏移的时间。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "采集时间缺少时区")
    return parsed.astimezone(timezone.utc)


def _project_path(value):
    # 旧 manifest 在 Windows 使用反斜线；也能在其他系统读取这些历史文件。
    path = (ROOT / str(value).replace("\\", "/")).resolve()
    _require(path.is_relative_to(ROOT), "来源文件路径超出项目目录")
    return path


def open_readonly(db_path=DEFAULT_DB):
    """mode=ro 阻止数据库写入，并避免误输入路径时新建空数据库。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"没有找到已完成入库的数据库：{path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    # 即使其他模块拿到连接，也只能 SELECT，不能 ATTACH 创建另一个数据库。
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
               sqlite3.SQLITE_RECURSIVE}
    connection.set_authorizer(lambda action, arg1, arg2, database, trigger:
                              sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    return connection


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_batch(batch, database_rows, totals=False):
    """四层校验：成功记录、文件摘要、官方原始单元格、SQLite 全字段。"""
    batch_id = batch["batch_id"]
    _require(re.fullmatch(r"\d{8}T\d{6}_\d{6}Z", batch_id) is not None, "批次 ID 格式异常")
    manifest = _read_json(ROOT / "outputs" / batch_id / "manifest.json")
    report = _read_json(ROOT / "outputs" / batch_id / "validation_report.json")
    _require(manifest.get("status") == "success" and manifest.get("run_id") == batch_id,
             "采集未成功或批次身份不一致")
    _require(report.get("validation_passed") is True, "该批次的验证报告未通过")
    version = manifest.get("schema_version", 1)
    _require(version in (1, 2), "不支持的采集清单版本")
    _require(batch.get("schema_version", 1) == version
             and batch.get("source_id", "EUROSTAT") == manifest.get("source_id", "EUROSTAT") == "EUROSTAT"
             and batch.get("scope_id", "new_passenger_registrations") ==
                 manifest.get("scope_id", "new_passenger_registrations") == "new_passenger_registrations",
             "来源身份、统计范围或清单版本不一致")
    artifact = manifest.get("artifacts", {}).get("total")
    if totals:
        _require(artifact is not None and report.get("totals", {}).get("validation_passed") is True,
                 "TOTAL 来源产物或验证报告缺失")
        report = {**report, **report["totals"]}
    csv_metadata = artifact if totals else manifest
    energy_types = TOTAL_TYPES if totals else ENERGY_TYPES
    _require(manifest.get("dataset") == report.get("dataset") == batch["dataset"]
             == "road_eqr_carpda", "数据集代码不一致")
    _require(manifest.get("http_status") == 200, "没有成功的完整官方 API 响应")
    request = urlsplit(manifest["request_url"])
    _require(request.scheme == "https" and request.netloc == "ec.europa.eu"
             and request.path == "/eurostat/api/dissemination/statistics/1.0/data/road_eqr_carpda",
             "请求地址不是本项目的 Eurostat 官方 API")
    _require(manifest["source_page"] ==
             "https://ec.europa.eu/eurostat/databrowser/product/page/road_eqr_carpda",
             "官方数据浏览页地址不一致")
    retrieved = _utc(manifest["retrieved_at_utc"])
    _require(_utc(manifest["requested_at_utc"]) <= retrieved, "采集时间顺序异常")
    _utc(batch["imported_at_utc"])
    for key in ("dataset", "source_page", "request_url", "requested_at_utc", "retrieved_at_utc",
                "raw_sha256", "csv_sha256"):
        _require(batch[key] == manifest[key], f"批次元数据与 manifest 不一致：{key}")
    raw_path = _project_path(manifest["raw_file"])
    csv_path = _project_path(csv_metadata["csv_file"])
    _require(raw_path.parent == ROOT / "data" / "raw" / batch_id, "原始响应不属于该批次")
    _require(csv_path.parent == ROOT / "data" / "processed" / batch_id, "CSV 不属于该批次")
    for path, path_key, hash_key in ((raw_path, "raw_file", "raw_sha256"),
                                     (csv_path, "csv_file", "csv_sha256")):
        if not (totals and path_key == "csv_file"):
            _require(_project_path(batch[path_key]) == path, f"数据库文件路径不一致：{path_key}")
        expected_hash = csv_metadata[hash_key] if path_key == "csv_file" else manifest[hash_key]
        _require(sha256(path.read_bytes()).hexdigest() == expected_hash,
                 f"来源文件摘要不一致：{path.name}")
    raw = _read_json(raw_path)
    _require(raw.get("class") == "dataset" and raw.get("version") == "2.0"
             and raw.get("source") == "ESTAT"
             and raw.get("extension", {}).get("id", "").lower() == batch["dataset"],
             "官方原始响应身份或格式不一致")
    dimensions = raw["id"]
    _require(len(dimensions) == 5 and set(dimensions) == {"freq", "unit", "mot_nrg", "geo", "time"},
             "官方数据维度变更，需要复核")
    _require(report["dimensions"] == dimensions and report["dimension_sizes"] == raw["size"],
             "验证报告中的维度与原始响应不一致")
    _require(report["label"] == raw["label"], "验证报告中的数据集统计说明不一致")
    total_cells = math.prod(raw["size"])
    for name in ("value", "status"):
        values = raw.get(name, {})
        _require(isinstance(values, (dict, list)), f"原始 {name} 不是数组或稀疏字典")
        if isinstance(values, list):
            _require(len(values) == total_cells, f"原始 {name} 数组长度不一致")
        else:
            _require(all(str(key).isdigit() and 0 <= int(key) < total_cells for key in values),
                     f"原始 {name} 包含无效单元格索引")
    axes = {}
    for name, size in zip(dimensions, raw["size"], strict=True):
        index = raw["dimension"][name]["category"]["index"]
        if isinstance(index, list):
            index = {code: position for position, code in enumerate(index)}
        _require(isinstance(index, dict) and sorted(index.values()) == list(range(size)),
                 "原始维度索引异常")
        axes[name] = index
    # 国家从官方原始响应读取，未来增添真实国家不会被 DE/FR 写死限制。
    _require(axes["geo"] and set(axes["freq"]) == {"A"} and set(axes["unit"]) == {"NR"},
             "国家、年度频率或辆数单位不符合分析口径")
    years = sorted(int(year) for year in axes["time"])
    _require(years and min(years) >= 2022 and max(years) <= retrieved.year,
             "观察年份超出采集范围")
    _require(report["years_returned"] == years, "验证报告中的年份不一致")
    query = parse_qs(request.query)
    _require(set(query.get("geo", [])) == set(axes["geo"]), "请求国家与原始响应不一致")
    if version == 2:
        declared = manifest["request_scope"]
        _require(set(declared["countries"]) == set(axes["geo"])
                 and declared["start_year"] == 2022
                 and set(declared["energy_codes"]) == set(ENERGY_TYPES)
                 and bool(declared["include_totals"]) == (artifact is not None), "采集范围声明不一致")
        if declared["end_year"] is not None:
            _require(max(years) <= declared["end_year"]
                     and query.get("untilTimePeriod") == [str(declared["end_year"])], "截止年份声明不一致")
    labels = raw["dimension"]["mot_nrg"]["category"]["label"]
    for code, (label, _, _) in energy_types.items():
        _require(code in axes["mot_nrg"] and labels.get(code, "").strip() == label,
                 f"动力类别口径变化：{code}")
    _require(raw["updated"] == batch["source_updated"] == report["source_updated"],
             "官方更新时间不一致")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(tuple(reader.fieldnames or ()) == CSV_COLUMNS, "CSV 字段结构变化")
        csv_rows = list(reader)
    expected_count = artifact["record_count"] if totals else batch["record_count"]
    _require(bool(csv_rows) and len(csv_rows) == report["rows"] == expected_count,
             "CSV、数据库和验证报告的记录数量不一致")
    expected_rows = []
    seen = set()
    flag_labels = raw.get("extension", {}).get("status", {}).get("label", {})
    for row in csv_rows:
        _require(None not in row and None not in row.values(), "CSV 单元格缺失或多余")
        key = (row["geo"], row["year"], row["mot_nrg"])
        _require(key not in seen and row["geo"] in axes["geo"]
                 and row["year"] in axes["time"] and row["mot_nrg"] in energy_types,
                 "CSV 含重复或未经核实的统计组合")
        seen.add(key)
        coordinates = {**row, "time": row["year"]}
        position = 0
        for name, size in zip(dimensions, raw["size"], strict=True):
            _require(coordinates[name] in axes[name], f"无效统计维度：{name}")
            position = position * size + axes[name][coordinates[name]]
        def cell(name):
            values = raw.get(name, {})
            return values.get(str(position)) if isinstance(values, dict) else values[position]
        value = cell("value")
        _require(value is None or (type(value) is int and value >= 0), "原始数量不是非负整数")
        status = cell("status") or ""
        expected = {
            **row, "dataset": batch["dataset"], "freq": "A", "unit": "NR",
            "country": raw["dimension"]["geo"]["category"]["label"][row["geo"]],
            "mot_nrg_label": labels[row["mot_nrg"]].strip(),
            "powertrain": energy_types[row["mot_nrg"]][1],
            "powertrain_zh": energy_types[row["mot_nrg"]][2],
            "value": "" if value is None else str(value), "is_missing": str(value is None),
            "status": status, "status_label": flag_labels.get(status,
                "" if not status else "See raw Eurostat status metadata"),
            "source_updated": raw["updated"], "retrieved_at_utc": manifest["retrieved_at_utc"],
            "source_url": manifest["request_url"],
        }
        _require(row == expected, f"CSV 与官方原始单元格不一致：{key}")
        expected_rows.append({"batch_id": batch_id, **row, "year": int(row["year"]),
                              "value": value, "is_missing": int(value is None)})
    _require(seen == set(product(axes["geo"], axes["time"], energy_types)), "本批次的统计组合不完整")
    _require(report["missing_rows"] == sum(row["is_missing"] for row in expected_rows)
             and report["observed_rows"] == sum(not row["is_missing"] for row in expected_rows),
             "验证报告中的缺失统计不一致")
    expected_flags = [{key: row[key] for key in ("geo", "year", "mot_nrg", "status", "status_label")}
                      for row in expected_rows if row["status"]]
    expected_missing = [{key: row[key] for key in ("geo", "year", "mot_nrg")}
                        for row in expected_rows if row["is_missing"]]
    def canonical(items):
        return sorted(json.dumps(item, sort_keys=True, ensure_ascii=False) for item in items)
    _require(canonical(report["flagged_observations"]) == canonical(expected_flags)
             and canonical(report["missing_observations"]) == canonical(expected_missing),
             "验证报告中的官方质量标记不一致")
    expected = {tuple(row[key] for key in ROW_COLUMNS) for row in expected_rows}
    actual = {tuple(row[key] for key in ROW_COLUMNS) for row in database_rows}
    _require(len(database_rows) == len(expected_rows) and actual == expected,
             "SQLite 与已验证 CSV 的记录或字段不一致")


def _batch_and_rows(connection, batch_id):
    batch_row = connection.execute(
        "SELECT * FROM collection_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    _require(batch_row is not None, f"数据库中没有这个批次：{batch_id}")
    rows = connection.execute(
        "SELECT * FROM vehicle_registrations WHERE batch_id = ? ORDER BY geo, year, mot_nrg",
        (batch_id,)).fetchall()
    batch = dict(batch_row)
    manifest = _read_json(ROOT / "outputs" / batch_id / "manifest.json")
    batch.update(source_id="EUROSTAT", scope_id="new_passenger_registrations", schema_version=1)
    has_sources = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_sources'").fetchone()
    source = connection.execute("SELECT * FROM batch_sources WHERE batch_id = ?", (batch_id,)).fetchone() if has_sources else None
    if source is not None:
        batch.update(dict(source))
    elif manifest.get("schema_version", 1) == 2:
        raise ValueError("新批次缺少数据库来源身份")
    _verify_batch(batch, rows)
    artifact = manifest.get("artifacts", {}).get("total")
    if artifact is not None:
        _require(source is not None and source["total_record_count"] == artifact["record_count"]
                 and source["total_csv_sha256"] == artifact["csv_sha256"]
                 and _project_path(source["total_csv_file"]) == _project_path(artifact["csv_file"]),
                 "TOTAL 来源文件元数据与数据库不一致")
        total_rows = connection.execute("SELECT * FROM total_vehicle_registrations WHERE batch_id = ? ORDER BY geo, year",
                                        (batch_id,)).fetchall()
        _verify_batch(batch, total_rows, totals=True)
    elif source is not None:
        _require(source["total_record_count"] == 0 and source["total_csv_file"] is None
                 and source["total_csv_sha256"] is None, "未声明 TOTAL 却有数据库来源记录")
        _require(connection.execute("SELECT COUNT(*) FROM total_vehicle_registrations WHERE batch_id = ?",
                                    (batch_id,)).fetchone()[0] == 0, "未声明 TOTAL 却有分母观察")
    return {**batch, "validation_passed": True}, [dict(row) for row in rows]


def list_verified_batches(db_path=DEFAULT_DB):
    """验证失败的批次不进入选择器；返回具体原因，界面可以如实提示。"""
    verified, warnings = [], []
    with closing(open_readonly(db_path)) as connection:
        batch_ids = [row[0] for row in connection.execute("SELECT batch_id FROM collection_batches")]
        for batch_id in batch_ids:
            try:
                batch, _ = _batch_and_rows(connection, batch_id)
                verified.append(batch)
            except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                warnings.append(f"批次 {batch_id} 未通过离线验证：{exc}")
    verified.sort(key=lambda item: _utc(item["retrieved_at_utc"]), reverse=True)
    return verified, warnings


def load_batch(batch_id, db_path=DEFAULT_DB):
    """验证来源后返回数据库中的数值；CSV 仅作证据核对，不作展示数据源。"""
    with closing(open_readonly(db_path)) as connection:
        batch, rows = _batch_and_rows(connection, batch_id)
    frame = pd.DataFrame.from_records(rows, columns=ROW_COLUMNS)
    # 可空整数保留 NULL；不会把缺失误显示为 0 或将辆数转成带小数的文本。
    frame["value"] = pd.array(frame["value"], dtype="Int64")
    return batch, frame


def load_totals(batch_id, db_path=DEFAULT_DB):
    """新分母只从独立 TOTAL 表读取；旧批次没有分母时返回相同18列的空表。"""
    with closing(open_readonly(db_path)) as connection:
        batch, _ = _batch_and_rows(connection, batch_id)
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='total_vehicle_registrations'").fetchone()
        rows = connection.execute("SELECT * FROM total_vehicle_registrations WHERE batch_id = ? ORDER BY geo, year",
                                  (batch_id,)).fetchall() if exists else []
    frame = pd.DataFrame.from_records([dict(row) for row in rows], columns=ROW_COLUMNS)
    frame["value"] = pd.array(frame["value"], dtype="Int64")
    return frame
