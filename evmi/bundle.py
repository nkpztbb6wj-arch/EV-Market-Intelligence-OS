"""为未来报告提供可复核 JSON；这里只组织证据，不调用 AI。"""

from datetime import datetime, timezone
import json
import math
from numbers import Integral, Real

import pandas as pd

from evmi import analytics
from evmi.markets import TARGET_COUNTRIES, country_group, powertrain_mapping


def clean_json(value):
    """JSON 不允许 NaN/Infinity；pandas 缺失统一转为 null，有效零保留。"""
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if value is None or value is pd.NA or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        if not math.isfinite(float(value)):
            raise ValueError("分析 JSON 出现非有限数值")
        return float(value)
    return value


def observation_id(row):
    return ":".join(str(row[key]) for key in (
        "batch_id", "dataset", "geo", "year", "freq", "unit", "mot_nrg"))


def build_bundle(batch, frame, totals, raw, manifest, countries=None, exclude_imputed=False):
    """所有计算使用单批 SQLite 数据；原始响应只用来提供单元格证据位置。"""
    # 接口本身也检查来源合同，不能只依赖 CLI 的正确调用顺序。
    if (batch.get("dataset") != "road_eqr_carpda" or batch.get("source_id", "EUROSTAT") != "EUROSTAT"
            or batch.get("scope_id", "new_passenger_registrations") != "new_passenger_registrations"
            or manifest.get("run_id") != batch.get("batch_id")
            or manifest.get("dataset") != batch.get("dataset")
            or manifest.get("request_url") != batch.get("request_url")
            or manifest.get("source_id", "EUROSTAT") != "EUROSTAT"
            or raw.get("source") != "ESTAT"
            or raw.get("extension", {}).get("id", "").lower() != batch.get("dataset")
            or raw.get("updated") != batch.get("source_updated")):
        raise ValueError("分析包批次、来源或范围合同不一致")
    for observations, codes in ((frame, set(analytics.ENERGIES)), (totals, {"TOTAL"})):
        if observations.duplicated(list(analytics.OBSERVATION_KEYS)).any():
            raise ValueError("分析包含重复观察")
        for row in observations.to_dict("records"):
            if (row["batch_id"] != batch["batch_id"] or row["dataset"] != batch["dataset"]
                    or row["freq"] != "A" or row["unit"] != "NR" or row["mot_nrg"] not in codes
                    or row.get("source_id", "EUROSTAT") != "EUROSTAT"
                    or row.get("scope_id", "new_passenger_registrations") != "new_passenger_registrations"
                    or row["source_url"] != batch["request_url"]
                    or row["source_updated"] != batch["source_updated"]
                    or row["retrieved_at_utc"] != batch["retrieved_at_utc"]):
                raise ValueError("分析包观察混入其他批次、来源或统计口径")
    countries = list(dict.fromkeys(countries if countries is not None else sorted(frame.geo.unique())))
    prepared = analytics.prepare_observations(frame, exclude_imputed)
    years = sorted(int(y) for y in frame.year.unique())
    facts, lookup = [], {}
    for row in pd.concat([frame, totals], ignore_index=True).to_dict("records"):
        position = 0
        coordinates = {**row, "time": str(row["year"])}
        for dimension, size in zip(raw["id"], raw["size"], strict=True):
            index = raw["dimension"][dimension]["category"]["index"]
            offset = index.index(coordinates[dimension]) if isinstance(index, list) else index[coordinates[dimension]]
            position = position * size + offset
        key = observation_id(row)
        lookup[(row["geo"], int(row["year"]), row["mot_nrg"])] = key
        pointer = f"/value/{position}"
        values = raw.get("value", {})
        present = str(position) in values if isinstance(values, dict) else values[position] is not None
        raw_value = values.get(str(position)) if isinstance(values, dict) else values[position]
        flags = raw.get("status", {})
        raw_status = (flags.get(str(position)) if isinstance(flags, dict) else flags[position]) or ""
        if (bool(row["is_missing"]) != (raw_value is None)
                or (raw_value is not None and (pd.isna(row["value"]) or row["value"] != raw_value))
                or (raw_value is None and not pd.isna(row["value"])) or row["status"] != raw_status):
            raise ValueError("分析包观察与官方原始单元格不一致")
        facts.append({"id": key, "kind": "official_fact", **row,
                      "source_id": "EUROSTAT", "membership": country_group(row["geo"]),
                      "mapping": powertrain_mapping("EUROSTAT", row["mot_nrg"]),
                      "scope_id": "new_passenger_registrations",
                      "quality": {"official_status": row["status"],
                                  "is_imputed": "i" in row["status"], "is_missing": bool(row["is_missing"])},
                      "evidence": {"raw_file": batch["raw_file"], "raw_sha256": batch["raw_sha256"],
                                   "cell_position": position, "value_pointer": pointer if present else None,
                                   "source_url": row["source_url"]}})
    metrics, observations, aggregates = [], [], []
    for year in years:
        ranked = analytics.country_rankings(prepared, totals, countries, year, exclude_imputed)
        for r in ranked.to_dict("records"):
            geo = r["geo"]
            metrics.append({"kind": "calculated_result", **r,
                            "batch_id": batch["batch_id"], "freq": "A", "unit": "NR",
                            "input_refs": {"current_bev": lookup.get((geo, year, "ELC")),
                                           "previous_bev": lookup.get((geo, year - 1, "ELC")),
                                           "current_total": lookup.get((geo, year, "TOTAL"))},
                            "formulas": {"yoy_pct": "100*(current_bev-previous_bev)/previous_bev",
                                         "delta": "current_bev-previous_bev",
                                         "bev_share_pct": "100*current_bev/current_total"},
                            "computation_notes": "同比严格匹配日历上年；分母缺失、质量排除或为零时为null；排名仅针对所选有效国家。"})
        for insight in analytics.build_insights(prepared, countries, analytics.ENERGIES, year):
            reference_years = {year, year - 1}
            if insight["reversal"]:
                reference_years.add(year - 2)
            if insight["is_max_change"]:
                # “最大变化”需要本系列所有可比年份的输入，而不只当前两年。
                candidates = prepared.loc[prepared.geo.eq(insight["geo"])
                    & prepared.mot_nrg.eq(insight["mot_nrg"]) & prepared.delta.notna()]
                for candidate_year in candidates.year:
                    reference_years.update((int(candidate_year), int(candidate_year) - 1))
            refs = [lookup[(insight["geo"], y, insight["mot_nrg"])]
                    for y in sorted(reference_years) if (insight["geo"], y, insight["mot_nrg"]) in lookup]
            observations.append({**insight, "kind": "data_observation",
                                 "input_refs": refs})
        aggregate = analytics.market_share(prepared, totals, countries, year, exclude_imputed)
        aggregate.update(kind="calculated_result", countries=countries,
                         formula="100*sum(current_bev)/sum(current_total)",
                         input_refs={"bev": [lookup[(g, year, "ELC")] for g in countries if (g, year, "ELC") in lookup],
                                     "total": [lookup[(g, year, "TOTAL")] for g in countries if (g, year, "TOTAL") in lookup]})
        aggregates.append(aggregate)
    result = {
        "schema_version": "1.1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "batch_id": batch["batch_id"], "source_id": "EUROSTAT", "scope_id": "new_passenger_registrations",
        "scope": {"countries": countries, "target_countries": list(TARGET_COUNTRIES), "years": years,
                  "freq": "A", "unit": "NR", "energy_codes": list(analytics.ENERGIES), "total_code": "TOTAL",
                  "exclude_imputed": bool(exclude_imputed), "market_label": "所选国家范围"},
        "sources": [{"name": "Eurostat", "dataset": batch["dataset"],
                     "source_page": batch["source_page"], "request_url": batch["request_url"],
                     "source_updated": batch["source_updated"], "retrieved_at_utc": batch["retrieved_at_utc"],
                     "raw_file": batch["raw_file"], "raw_sha256": batch["raw_sha256"],
                     "csv_file": batch["csv_file"], "csv_sha256": batch["csv_sha256"],
                     "artifacts": manifest.get("artifacts", {})}],
        "official_facts": facts, "calculated_metrics": metrics, "data_observations": observations,
        "unverified_business_hypotheses": [],
        "coverage": analytics.coverage_by_country(prepared, countries, years).to_dict("records"),
        "aggregate_bev_share": aggregates,
        "comparability": {"same_batch_required": True, "same_source_required": True,
                          "vehicle_scope": "年度首次注册新乘用车，进口二手车不计入",
                          "uk_integration": "pending_assessment_and_user_confirmation",
                          "phev_total_not_split": True},
        "limitations": ["仅代表所选国家，不代表欧盟或整个欧洲市场。",
                        "注册量不等于销量或保有量。", "官方插补不是项目自行补值；原标记保留。",
                        "跨国方法可能存在差异；不据此推断政策原因、预测或投资建议。",
                        "2022年缺少同批2021年基准，同比不可计算。",
                        "英国尚未接入；不把PHEV合计拆分为汽油与柴油PHEV。"],
    }
    result = clean_json(result)
    # 序列化校验在写文件之前进行，避免产生不可供下游解析的 JSON。
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result
