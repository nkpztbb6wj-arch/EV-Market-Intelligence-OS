"""国家身份与来源分类约定；未批准的来源不能自动加入可比市场。"""

COUNTRY_NAMES = {
    "DE": "德国", "FR": "法国", "IT": "意大利", "ES": "西班牙", "NL": "荷兰",
    "SE": "瑞典", "NO": "挪威", "BE": "比利时", "PL": "波兰", "DK": "丹麦", "GB": "英国",
}
EU_COUNTRIES = frozenset({"DE", "FR", "IT", "ES", "NL", "SE", "BE", "PL", "DK"})
TARGET_COUNTRIES = tuple(COUNTRY_NAMES)
EUROSTAT_COUNTRIES = tuple(code for code in TARGET_COUNTRIES if code != "GB")


def country_group(code):
    """身份标记只适用于本项目目标范围，不把未知国家直接归为非欧盟。"""
    if code in EU_COUNTRIES:
        return "EU"
    if code in {"NO", "GB"}:
        return "NON_EU"
    return "UNKNOWN"


def powertrain_mapping(source_id, original_code):
    """保留来源原分类；SMMT PHEV 合计绝不能拆成汽油/柴油 PHEV。"""
    maps = {
        "EUROSTAT": {"ELC": "ELC", "ELC_PET_PI": "ELC_PET_PI",
                     "ELC_DIE_PI": "ELC_DIE_PI", "TOTAL": "TOTAL"},
        "SMMT": {"BEV": "ELC", "PHEV": "PHEV_TOTAL", "TOTAL": "TOTAL"},
    }
    normalized = maps.get(source_id, {}).get(original_code)
    return {"source_id": source_id, "original_code": original_code,
            "normalized_code": normalized,
            "classification_comparable": normalized in {"ELC", "ELC_PET_PI", "ELC_DIE_PI", "TOTAL"},
            # 映射语义不等于准许接入：SMMT 仍需来源/范围/许可审批。
            "integration_approved": source_id == "EUROSTAT",
            "reason": "aggregate_phev_not_split" if normalized == "PHEV_TOTAL"
                      else "unknown_classification" if normalized is None else "mapped"}


def comparable_pair(numerator, denominator):
    """当前只计算同批 Eurostat BEV/TOTAL；未来跨源需显式新增经批准的合同。"""
    for field in ("batch_id", "dataset", "geo", "year", "freq", "unit"):
        if numerator.get(field) != denominator.get(field):
            return False
    source_left = numerator.get("source_id", "EUROSTAT" if numerator.get("dataset") == "road_eqr_carpda" else None)
    source_right = denominator.get("source_id", "EUROSTAT" if denominator.get("dataset") == "road_eqr_carpda" else None)
    return (source_left == source_right == "EUROSTAT"
            and numerator.get("dataset") == "road_eqr_carpda"
            and numerator.get("freq") == "A" and numerator.get("unit") == "NR"
            and numerator.get("scope_id", "new_passenger_registrations") ==
                denominator.get("scope_id", "new_passenger_registrations") == "new_passenger_registrations"
            and numerator.get("mot_nrg") == "ELC" and denominator.get("mot_nrg") == "TOTAL")
