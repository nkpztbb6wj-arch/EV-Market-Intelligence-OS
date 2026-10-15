"""指标计算层：先在完整批次计算，再筛选展示，保留所有原始字段。"""

from itertools import product

import pandas as pd

from evmi.markets import COUNTRY_NAMES, country_group, comparable_pair


ENERGIES = ("ELC", "ELC_PET_PI", "ELC_DIE_PI")
ENERGY_NAMES = {"ELC": "纯电", "ELC_PET_PI": "汽油插混", "ELC_DIE_PI": "柴油插混"}
SERIES_KEYS = ("batch_id", "dataset", "geo", "freq", "unit", "mot_nrg")
OBSERVATION_KEYS = SERIES_KEYS + ("year",)


def _present(value):
    return value is not None and not pd.isna(value)


def _imputed(status):
    return _present(status) and "i" in str(status)


def _unique(items):
    return list(dict.fromkeys(items))


def _one_scope(frame):
    """汇总前防止把两个采集快照、两个数据集或不同单位相加。"""
    if not frame.empty:
        for column in ("batch_id", "dataset", "freq", "unit"):
            if frame[column].nunique(dropna=False) != 1:
                raise ValueError(f"汇总必须选定同一个 {column}，不能混合批次或统计口径")


def prepare_observations(frame, exclude_imputed=False):
    """真实量保留不变；同比严格匹配同系列的日历上一年。"""
    required = set(OBSERVATION_KEYS) | {"value", "is_missing", "status", "country"}
    if not required.issubset(frame.columns):
        raise ValueError(f"数据缺少字段：{sorted(required - set(frame.columns))}")
    if frame.duplicated(list(OBSERVATION_KEYS)).any():
        raise ValueError("同批次中出现重复观察，不能安全计算同比")
    prepared = frame.copy(deep=True).sort_values(list(SERIES_KEYS) + ["year"]).reset_index(drop=True)
    reasons = []
    for row in prepared.to_dict("records"):
        if row["is_missing"] or not _present(row["value"]):
            reasons.append("current_missing")
        elif exclude_imputed and _imputed(row["status"]):
            reasons.append("current_excluded")
        else:
            reasons.append("")
    prepared["excluded_reason"] = reasons
    prepared["eligible"] = prepared["excluded_reason"].eq("")
    records = prepared.to_dict("records")
    lookup = {tuple(row[key] for key in OBSERVATION_KEYS): row for row in records}
    calculated = []
    for row in records:
        previous_key = tuple(row[key] for key in SERIES_KEYS) + (int(row["year"]) - 1,)
        previous = lookup.get(previous_key)
        result = {"previous_value": previous["value"] if previous else None,
                  "previous_status": previous["status"] if previous else None,
                  "previous_eligible": bool(previous and previous["eligible"]),
                  "delta": None, "yoy_pct": None, "yoy_status": "ok",
                  "direction": "unavailable"}
        if not row["eligible"]:
            result["yoy_status"] = row["excluded_reason"]
        elif previous is None:
            result["yoy_status"] = "no_previous_year"
        elif not previous["eligible"]:
            result["yoy_status"] = ("previous_missing" if previous["excluded_reason"] == "current_missing"
                                    else "previous_excluded")
        else:
            delta = int(row["value"]) - int(previous["value"])
            result["delta"] = delta
            result["direction"] = "growth" if delta > 0 else "decline" if delta < 0 else "flat"
            if int(previous["value"]) == 0:
                # 前一年为零时绝对净增仍有意义，但增长率不能除以零。
                result["yoy_status"] = "previous_zero"
            else:
                result["yoy_pct"] = 100.0 * delta / int(previous["value"])
        calculated.append(result)
    for column in ("previous_value", "delta"):
        prepared[column] = pd.array([row[column] for row in calculated], dtype="Int64")
    prepared["yoy_pct"] = pd.array([row["yoy_pct"] for row in calculated], dtype="Float64")
    for column in ("previous_status", "previous_eligible", "yoy_status", "direction"):
        prepared[column] = [row[column] for row in calculated]
    prepared["previous_eligible"] = prepared["previous_eligible"].astype(bool)
    prepared["reversal"] = False
    prepared["is_max_change"] = False
    # 最大变化按每个国家×动力系列的绝对净增比较；相同最大值全部保留。
    for _, group in prepared.groupby(list(SERIES_KEYS), sort=False, dropna=False):
        usable = group.loc[group["delta"].notna()]
        if not usable.empty:
            maximum = usable["delta"].abs().max()
            # 所有年均不变时，不把每个 0 变化包装为“最大变化”。
            if maximum > 0:
                prepared.loc[usable.index[usable["delta"].abs().eq(maximum)], "is_max_change"] = True
        by_year = {int(row["year"]): row for row in group.to_dict("records")}
        for index, row in group.iterrows():
            previous = by_year.get(int(row["year"]) - 1)
            if previous and _present(row["yoy_pct"]) and _present(previous["yoy_pct"]):
                # 必须连续两年均有可比同比，且非零方向翻转，才称为趋势反转。
                prepared.at[index, "reversal"] = float(row["yoy_pct"]) * float(previous["yoy_pct"]) < 0
    prepared.attrs["exclude_imputed"] = bool(exclude_imputed)
    return prepared


def filter_observations(prepared, countries, energies, years):
    """仅筛选已经算好的结果；不会因隐藏上一年而改变同比。"""
    return prepared.loc[prepared["geo"].isin(countries)
                        & prepared["mot_nrg"].isin(energies)
                        & prepared["year"].isin(years)].copy()


def aggregate_year(prepared, countries, energies, year):
    """先求同一组分项合计再计算同比，不能平均各分项增长率。"""
    _one_scope(prepared)
    countries, energies = _unique(countries), _unique(energies)
    expected = set(product(countries, energies))
    count = len(expected)
    current = prepared.loc[prepared["year"].eq(year) & prepared["geo"].isin(countries)
                           & prepared["mot_nrg"].isin(energies)]
    previous = prepared.loc[prepared["year"].eq(year - 1) & prepared["geo"].isin(countries)
                            & prepared["mot_nrg"].isin(energies)]
    def annual(rows):
        available = rows.loc[rows["eligible"]]
        keys = set(zip(available["geo"], available["mot_nrg"]))
        complete = bool(count and keys == expected and len(available) == count)
        observed_sum = int(available["value"].sum()) if not available.empty else None
        return complete, observed_sum, len(available)
    current_complete, observed, available_count = annual(current)
    previous_complete, previous_observed, _ = annual(previous)
    result = {"value": observed if current_complete else None,
              "previous_value": previous_observed if previous_complete else None,
              "observed_sum": observed, "delta": None, "yoy_pct": None,
              "reason": "ok", "complete": current_complete,
              "previous_complete": previous_complete,
              # 空选择时 pandas 3 的字符串列 sum() 可能返回空字符串；
              # Python 的 sum(generator) 始终返回整数 0，意大利等未采集国家不报错。
              "imputed_count": sum(_imputed(status) for status in current["status"]),
              "expected_count": count, "available_count": available_count,
              "excluded_count": int((~current["eligible"]).sum()), "year": int(year)}
    if not count:
        result["reason"] = "no_selection"
    elif not current_complete:
        result["reason"] = "current_incomplete"
    elif not previous_complete:
        result["reason"] = "no_previous_year" if previous.empty else "previous_incomplete"
    else:
        result["delta"] = observed - previous_observed
        if previous_observed == 0:
            result["reason"] = "previous_zero"
        else:
            result["yoy_pct"] = 100.0 * result["delta"] / previous_observed
    return result


def structure_by_year(prepared, countries):
    """结构分母固定为三类插电车型；普通混动或全新车不进入分母。"""
    _one_scope(prepared)
    output = []
    for geo in _unique(countries):
        national = prepared.loc[prepared["geo"].eq(geo)]
        for year in sorted(prepared["year"].unique()):
            annual = national.loc[national["year"].eq(year) & national["mot_nrg"].isin(ENERGIES)]
            lookup = {row["mot_nrg"]: row for row in annual.to_dict("records")}
            complete = all(code in lookup and lookup[code]["eligible"] for code in ENERGIES)
            total = sum(int(lookup[code]["value"]) for code in ENERGIES) if complete else None
            usable = complete and total > 0
            for code in ENERGIES:
                source = lookup.get(code, {})
                # 保留原始值；被排除或缺分项时占比不可计算，不悄悄改分母。
                row = {"geo": geo, "country": source.get("country", national["country"].iloc[0]
                       if not national.empty else geo), "year": int(year), "mot_nrg": code,
                       "value": source.get("value"), "share_pct":
                           100.0 * int(source["value"]) / total if usable else None,
                       "complete": bool(usable), "status": source.get("status", ""),
                       "eligible": bool(source.get("eligible", False)),
                       "powertrain_zh": source.get("powertrain_zh", ENERGY_NAMES[code]),
                       "source_url": source.get("source_url", ""),
                       "batch_id": source.get("batch_id", ""),
                       "reason": "ok" if usable else "all_zero" if complete else "incomplete_structure"}
                output.append(row)
    columns = ("geo", "country", "year", "mot_nrg", "value", "share_pct", "complete", "status",
               "eligible", "powertrain_zh", "source_url", "batch_id", "reason")
    result = pd.DataFrame.from_records(output, columns=columns)
    result["value"] = pd.array(result["value"], dtype="Int64")
    result["share_pct"] = pd.array(result["share_pct"], dtype="Float64")
    return result


def index_trends(prepared, base_year):
    """基年=100；同一真实系列的基年缺失、被排除或为零时不生成指数。"""
    result = prepared.copy(deep=True)
    records = result.to_dict("records")
    base = {tuple(row[key] for key in SERIES_KEYS): row
            for row in records if int(row["year"]) == int(base_year)}
    indices, statuses = [], []
    for row in records:
        baseline = base.get(tuple(row[key] for key in SERIES_KEYS))
        value, reason = None, "ok"
        if not row["eligible"]:
            reason = row["excluded_reason"]
        elif baseline is None or not _present(baseline["value"]):
            reason = "base_missing"
        elif not baseline["eligible"]:
            reason = "base_excluded"
        elif int(baseline["value"]) == 0:
            reason = "base_zero"
        else:
            value = 100.0 * int(row["value"]) / int(baseline["value"])
        indices.append(value)
        statuses.append(reason)
    result["index_value"] = pd.array(indices, dtype="Float64")
    result["index_status"] = statuses
    result["base_year"] = int(base_year)
    return result


def quadrant_data(prepared, year):
    """四象限规模门槛来自完整样本，随后做焦点筛选不会移动中位数线。"""
    _one_scope(prepared)
    annual = prepared.loc[prepared["year"].eq(year) & prepared["mot_nrg"].isin(ENERGIES)].copy()
    comparable = annual.loc[annual["eligible"] & annual["previous_eligible"]
                             & annual["yoy_pct"].notna()].copy()
    expected = set(product(prepared["geo"].unique(), ENERGIES))
    represented = set(zip(comparable["geo"], comparable["mot_nrg"]))
    threshold = float(comparable["previous_value"].median()) if represented == expected and expected else None
    comparable["scale_threshold"] = threshold
    def classify(row):
        if threshold is None:
            return "可比样本不完整，暂不划分规模象限"
        scale = "较大规模" if int(row["previous_value"]) >= threshold else "较小规模"
        movement = "扩张" if float(row["yoy_pct"]) > 0 else "收缩" if float(row["yoy_pct"]) < 0 else "持平"
        return f"{scale} / {movement}"
    comparable["quadrant"] = [classify(row) for row in comparable.to_dict("records")]
    return comparable, threshold


def build_insights(prepared, countries, energies, year):
    """分析提示只陈述观察与运算结果，不解释尚无证据的政策或商业原因。"""
    _one_scope(prepared)
    selected = filter_observations(prepared, countries, energies, [year])
    insights = []
    for row in selected.to_dict("records"):
        name = f"{row['country']} · {ENERGY_NAMES.get(row['mot_nrg'], row['mot_nrg'])}"
        if not _present(row["delta"]):
            continue
        delta = int(row["delta"])
        movement = "增加" if delta > 0 else "减少" if delta < 0 else "不变"
        text = f"{year} 年 {name} 注册量{movement}"
        text += f" {abs(delta):,} 辆" if delta else ""
        if _present(row["yoy_pct"]):
            text += f"，同比 {float(row['yoy_pct']):+.2f}%"
        else:
            text += "；上一年为零，同比不可计算"
        if row["reversal"]:
            text += "；连续年度增长方向发生反转"
        if row["is_max_change"]:
            text += "；为该系列本批次可比年份中最大绝对净变动"
        if _imputed(row["status"]) or _imputed(row["previous_status"]):
            text += "（比较包含官方插补值）"
        insights.append({"text": text + "。", "kind": "分析提示", "geo": row["geo"],
                         "mot_nrg": row["mot_nrg"], "year": int(year),
                         "source": {key: row.get(key, "") for key in
                                    ("batch_id", "dataset", "source_url", "source_updated", "retrieved_at_utc")},
                         "direction": row["direction"], "reversal": bool(row["reversal"]),
                         "is_max_change": bool(row["is_max_change"])})
    return insights


def compare_batches(old_frame, new_frame):
    """按统计组合对比两个快照，返回值/质量标记修订、新增和删除观察。"""
    _one_scope(old_frame)
    _one_scope(new_frame)
    keys = ("dataset", "geo", "year", "freq", "unit", "mot_nrg")
    for frame in (old_frame, new_frame):
        if frame.duplicated(list(keys)).any():
            raise ValueError("批次修订对比遇到重复统计组合")
    old = {tuple(row[key] for key in keys): row for row in old_frame.to_dict("records")}
    new = {tuple(row[key] for key in keys): row for row in new_frame.to_dict("records")}
    differences = []
    for key in sorted(set(old) | set(new)):
        previous, current = old.get(key), new.get(key)
        if previous is None:
            change = "added"
        elif current is None:
            change = "removed"
        else:
            def same(a, b):
                return (not _present(a) and not _present(b)) or (_present(a) and _present(b) and a == b)
            changed = [field for field in ("value", "is_missing", "status", "status_label")
                       if not same(previous[field], current[field])]
            if not changed:
                continue
            change = "revised"
        row = dict(zip(keys, key))
        row["change_type"] = change
        row["changed_fields"] = ",".join(changed) if change == "revised" else ""
        for prefix, source in (("old", previous), ("new", current)):
            for field in ("value", "is_missing", "status", "status_label", "batch_id", "source_url"):
                row[f"{prefix}_{field}"] = source.get(field) if source else None
        differences.append(row)
    columns = list(keys) + ["change_type", "changed_fields"] + [
        f"{prefix}_{field}" for prefix in ("old", "new")
        for field in ("value", "is_missing", "status", "status_label", "batch_id", "source_url")]
    result = pd.DataFrame.from_records(differences, columns=columns)
    for column in ("old_value", "new_value"):
        result[column] = pd.array(result[column], dtype="Int64")
    return result


def _share_pair(bev, total, exclude_imputed=False):
    """分子和分母分别检查；原始零、缺失、质量排除各有不同含义。"""
    if not bev or bev.get("is_missing") or not _present(bev.get("value")):
        return None, "current_missing"
    if exclude_imputed and _imputed(bev.get("status")):
        return None, "current_excluded"
    if not total or total.get("is_missing") or not _present(total.get("value")):
        return None, "total_missing"
    if not comparable_pair(bev, total):
        return None, "incomparable_scope"
    if exclude_imputed and _imputed(total.get("status")):
        return None, "total_excluded"
    if int(total["value"]) == 0:
        return None, "total_zero"
    if int(bev["value"]) > int(total["value"]):
        return None, "numerator_gt_total"
    return 100.0 * int(bev["value"]) / int(total["value"]), "ok"


def country_rankings(prepared, totals, countries, year, exclude_imputed=False):
    """四项排名仅使用有效可比值；缺失不排名，同值使用并列名次。"""
    if prepared.attrs.get("exclude_imputed", False) != bool(exclude_imputed):
        prepared = prepare_observations(prepared, exclude_imputed)
    _one_scope(prepared)
    _one_scope(totals)
    if not prepared.empty and (set(prepared["dataset"]) != {"road_eqr_carpda"}
            or set(prepared["freq"]) != {"A"} or set(prepared["unit"]) != {"NR"}
            or ("source_id" in prepared and set(prepared["source_id"]) != {"EUROSTAT"})):
        raise ValueError("排名来源或统计口径尚未通过可比性批准")
    for frame in (prepared, totals):
        if not frame.empty and frame.duplicated(list(OBSERVATION_KEYS)).any():
            raise ValueError("排名输入含重复观察")
    core = {r["geo"]: r for r in prepared.loc[prepared.geo.isin(countries)
            & prepared.year.eq(year) & prepared.mot_nrg.eq("ELC")].to_dict("records")}
    total_rows = {r["geo"]: r for r in totals.loc[totals.geo.isin(countries)
                  & totals.year.eq(year) & totals.mot_nrg.eq("TOTAL")].to_dict("records")}
    output = []
    for geo in _unique(countries):
        bev, denominator = core.get(geo), total_rows.get(geo)
        share, reason = _share_pair(bev, denominator, exclude_imputed)
        eligible = bool(bev and bev["eligible"] and not (exclude_imputed and _imputed(bev["status"])))
        total_eligible = bool(denominator and not denominator["is_missing"]
                              and _present(denominator["value"])
                              and not (exclude_imputed and _imputed(denominator["status"])))
        annual = prepared.loc[prepared.geo.eq(geo) & prepared.year.eq(year)
                              & prepared.mot_nrg.isin(ENERGIES)]
        output.append({"geo": geo, "country": COUNTRY_NAMES.get(geo, geo), "year": int(year),
                       "membership": country_group(geo),
                       "bev_value": int(bev["value"]) if eligible else None,
                       "total_value": int(denominator["value"]) if total_eligible else None,
                       "yoy_pct": bev["yoy_pct"] if eligible else None,
                       "delta": bev["delta"] if eligible else None,
                       "bev_share_pct": share, "share_status": reason,
                       "yoy_status": bev["yoy_status"] if bev else "current_missing",
                       "status": bev["status"] if bev else "",
                       "previous_status": bev["previous_status"] if bev else None,
                       "total_status": denominator["status"] if denominator else "",
                       "available_count": int(annual["eligible"].sum()), "expected_count": len(ENERGIES),
                       "source_id": "EUROSTAT" if bev else None,
                       "source_url": bev.get("source_url") if bev else None,
                       "total_source_url": denominator.get("source_url") if denominator else None})
    columns = ("geo", "country", "year", "membership", "bev_value", "total_value", "yoy_pct", "delta",
               "bev_share_pct", "share_status", "yoy_status", "status", "previous_status", "total_status",
               "available_count", "expected_count", "source_id", "source_url", "total_source_url")
    result = pd.DataFrame.from_records(output, columns=columns)
    for column in ("bev_value", "total_value", "delta"):
        result[column] = pd.array(result[column], dtype="Int64")
    for column in ("yoy_pct", "bev_share_pct"):
        result[column] = pd.array(result[column], dtype="Float64")
    for value_column, rank_column in (("bev_value", "registrations_rank"), ("yoy_pct", "yoy_rank"),
                                     ("delta", "delta_rank"), ("bev_share_pct", "share_rank")):
        result[rank_column] = result[value_column].rank(method="min", ascending=False, na_option="keep").astype("Int64")
    return result


def market_share(prepared, totals, countries, year, exclude_imputed=False):
    """所选国家合并占比=sum(BEV)/sum(TOTAL)，绝不平均国家百分比。"""
    ranked = country_rankings(prepared, totals, countries, year, exclude_imputed)
    expected = len(ranked)
    available = int(ranked["share_status"].eq("ok").sum())
    complete = bool(expected and available == expected)
    numerator = int(ranked["bev_value"].sum()) if complete else None
    denominator = int(ranked["total_value"].sum()) if complete else None
    reason = "ok" if complete else "no_selection" if not expected else (
        ranked["share_status"].iloc[0] if expected == 1 else "coverage_incomplete")
    return {"value": 100.0 * numerator / denominator if complete else None,
            "numerator": numerator, "denominator": denominator, "complete": complete,
            "reason": reason, "year": int(year), "expected_count": expected,
            "available_count": available,
            "imputed_count": sum(_imputed(r["status"]) + _imputed(r["total_status"])
                                  for r in ranked.to_dict("records"))}


def coverage_by_country(prepared, countries, years):
    """覆盖率针对预期三类组合；排除插补不改变原始缺失状态。"""
    _one_scope(prepared)
    output = []
    for geo, year in product(_unique(countries), _unique(years)):
        annual = prepared.loc[prepared.geo.eq(geo) & prepared.year.eq(year)
                              & prepared.mot_nrg.isin(ENERGIES)]
        observed = int(annual["value"].notna().sum())
        available = int(annual["eligible"].sum())
        output.append({"geo": geo, "country": COUNTRY_NAMES.get(geo, geo), "year": int(year),
                       "membership": country_group(geo), "expected_count": len(ENERGIES),
                       "observed_count": observed, "available_count": available,
                       "missing_count": len(ENERGIES) - observed,
                       "excluded_count": observed - available,
                       "imputed_count": sum(_imputed(s) for s in annual["status"]),
                       "coverage_pct": 100.0 * available / len(ENERGIES)})
    return pd.DataFrame.from_records(output, columns=("geo", "country", "year", "membership",
        "expected_count", "observed_count", "available_count", "missing_count", "excluded_count",
        "imputed_count", "coverage_pct"))
