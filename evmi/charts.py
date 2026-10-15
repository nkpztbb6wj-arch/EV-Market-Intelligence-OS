"""统一的 Plotly 图表层：只把真实记录与分析层的计算结果画出来。

图表的 customdata 前三列固定为国家、动力、年份，供 Streamlit 点选追溯。
所有缺失数值保留为 None，既不画成 0，也不连接缺失区间。
"""

from __future__ import annotations

from html import escape
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from evmi.markets import COUNTRY_NAMES, TARGET_COUNTRIES


POWERTRAIN_ORDER = ("ELC", "ELC_PET_PI", "ELC_DIE_PI")
POWERTRAIN_LABELS = {
    "ELC": "BEV · 纯电",
    "ELC_PET_PI": "PHEV · 汽油",
    "ELC_DIE_PI": "PHEV · 柴油",
    "ALL": "筛选动力合计",
    "SELECTED": "所选动力合计",
}
POWERTRAIN_SHORT = {"ELC": "BEV", "ELC_PET_PI": "PHEV 汽油", "ELC_DIE_PI": "PHEV 柴油"}
POWERTRAIN_LABELS.update(PHEV_TOTAL='PHEV 合计（计算）', TOTAL='全部首次注册（英国为约数）')
POWERTRAIN_SHORT.update(PHEV_TOTAL='PHEV 合计', TOTAL='TOTAL')
POWERTRAIN_COLORS = {"ELC": "#47C6EB", "ELC_PET_PI": "#F0B85B", "ELC_DIE_PI": "#A995E9"}
COUNTRY_DASHES = {code: ("solid", "dash", "dot", "dashdot", "longdash", "longdashdot")[index % 6]
                  for index, code in enumerate(TARGET_COUNTRIES)}
COUNTRY_SYMBOLS = {code: ("circle", "square", "triangle-up", "triangle-down", "pentagon", "hexagon", "cross", "x", "diamond", "star-triangle-up", "circle-open")[index % 11]
                   for index, code in enumerate(TARGET_COUNTRIES)}

# 颜色不仅用于外观：同一种动力在所有图里使用同一种颜色。
THEMES = {
    "dark": {
        "paper": "#111D30", "plot": "#111D30", "text": "#E4ECF7", "muted": "#A7B7CD",
        "grid": "#25364E", "border": "#334963", "gray": "#293A50", "positive": "#3CCEA4",
        "negative": "#F08092", "hover": "#1B2D45", "ocean": "#0D1726",
    },
    "light": {
        "paper": "#FFFFFF", "plot": "#FFFFFF", "text": "#182B45", "muted": "#586B83",
        "grid": "#E5ECF3", "border": "#BECDDC", "gray": "#E2E8EF", "positive": "#148C71",
        "negative": "#C04462", "hover": "#FFFFFF", "ocean": "#F3F6FA",
    },
}

# 这是地理定位代码，不是市场数据；新增国家仍必须来自 SQLite 的真实记录。
EUROPE_ISO3 = {
    "AL": "ALB", "AD": "AND", "AT": "AUT", "BE": "BEL", "BA": "BIH", "BG": "BGR",
    "BY": "BLR", "CH": "CHE", "CY": "CYP", "CZ": "CZE", "DE": "DEU", "DK": "DNK",
    "EE": "EST", "EL": "GRC", "GR": "GRC", "ES": "ESP", "FI": "FIN", "FR": "FRA",
    "HR": "HRV", "HU": "HUN", "IE": "IRL", "IS": "ISL", "IT": "ITA", "LI": "LIE",
    "LT": "LTU", "LU": "LUX", "LV": "LVA", "MC": "MCO", "MD": "MDA", "ME": "MNE",
    "MK": "MKD", "MT": "MLT", "NL": "NLD", "NO": "NOR", "PL": "POL", "PT": "PRT",
    "RO": "ROU", "RS": "SRB", "RU": "RUS", "SE": "SWE", "SI": "SVN", "SK": "SVK",
    "SM": "SMR", "TR": "TUR", "UA": "UKR", "UK": "GBR", "GB": "GBR", "VA": "VAT",
}

CALCULATION_STATUS = {
    "ok": "可计算", "no_previous_year": "本批次缺少上一年", "previous_zero": "上年为零，无法计算同比",
    "current_missing": "当年缺失", "previous_missing": "上年缺失", "current_excluded": "当年被质量筛选排除",
    "previous_excluded": "上年被质量筛选排除", "current_incomplete": "当年分项不完整", "previous_incomplete": "上年分项不完整",
    "incomplete_structure": "三类分项不完整", "all_zero": "三类总量为零，无法计算份额", "no_selection": "未选择分析范围",
    "total_missing": "缺少同批次全部新车分母", "total_excluded": "分母被质量筛选排除", "total_zero": "全部新车分母为零",
    "incomparable_scope": "分子分母口径不可比", "numerator_gt_total": "分子超过分母，需核查", "coverage_incomplete": "覆盖不完整",
}


def _palette(theme: str) -> dict[str, str]:
    return THEMES.get(theme, THEMES["dark"])


def _number(value: Any, *, pct: bool = False, signed: bool = False) -> str:
    """悬浮提示使用可读数字；缺失数据明确显示为“暂无”。"""
    if value is None or pd.isna(value):
        return "暂无"
    return f"{float(value):+.2f}%" if pct else f"{float(value):{'+,' if signed else ','}.0f}"


def _plain(value: Any, default: str = "") -> str:
    if value is None or pd.isna(value):
        return default
    return escape(str(value))


def _is_true(value: Any) -> bool:
    """pandas 的 pd.NA 不能直接当作布尔值，先识别缺失。"""
    return value is not None and not pd.isna(value) and bool(value)


def _country(row: dict[str, Any]) -> str:
    geo = str(row.get("geo", ""))
    return COUNTRY_NAMES.get(geo, _plain(row.get("country"), geo))


def _quality(row: dict[str, Any]) -> str:
    status = _plain(row.get("status"))
    eligibility = row.get("eligible")
    excluded = eligibility is not None and not pd.isna(eligibility) and not bool(eligibility)
    flag = "无官方质量标记" if not status else f"官方标记：{status}"
    if row.get("imputed_count") is not None and not pd.isna(row.get("imputed_count")):
        flag = f"含 {int(row['imputed_count'])} 条官方插补记录"
    if excluded:
        flag += " · 当前质量筛选排除"
    return flag


def _customdata(frame: pd.DataFrame) -> list[list[Any]]:
    """前三列保留机器识别代码；后面是给初学者可读的悬浮信息。"""
    output = []
    for row in frame.to_dict("records"):
        year = row.get("year")
        direction = {"growth": "增长", "decline": "下降", "flat": "持平", "unavailable": "暂无可比变化"}.get(row.get("direction"), "")
        hints = [direction] if direction else []
        if _is_true(row.get("reversal")):
            hints.append("连续年度增长方向反转")
        if _is_true(row.get("is_max_change")):
            hints.append("本系列批次内最大绝对净变动")
        calculation_status = row.get("yoy_status", row.get("reason", "未计算"))
        output.append([
            str(row.get("geo", "")), str(row.get("mot_nrg", "ALL")),
            int(year) if year is not None and not pd.isna(year) else None,
            _country(row), POWERTRAIN_LABELS.get(str(row.get("mot_nrg", "ALL")), _plain(row.get("powertrain_zh"))),
            _number(row.get("value")), _number(row.get("previous_value")), _number(row.get("delta"), signed=True),
            _number(row.get("yoy_pct"), pct=True), _quality(row),
            CALCULATION_STATUS.get(calculation_status, _plain(calculation_status, "未计算")), _plain(row.get("source_updated"), "见来源面板"),
            _plain(row.get("dataset"), "Eurostat · road_eqr_carpda") + ' · ' + _plain(row.get('scope_note'))
                + (' · 约数：公布千辆换算' if row.get('value_kind') == 'rounded_unit_conversion' else '')
                + (' · 项目计算，非独立官方值' if row.get('value_kind') == 'calculated' else ''),
            _plain(row.get("previous_status"), "无"),
            " · ".join(hints) or "见分析提示面板", _plain(row.get("source_url")),
        ])
    return output


HOVER = (
    "<b>%{customdata[3]} · %{customdata[4]} · %{customdata[2]}</b>"
    "<br>注册量（数值性质见来源）：%{customdata[5]} 辆"
    "<br>上一年注册量：%{customdata[6]} 辆"
    "<br>计算净增：%{customdata[7]} 辆 · 同比：%{customdata[8]}"
    "<br>%{customdata[9]} · 上年标记：%{customdata[13]}"
    "<br>同比状态：%{customdata[10]}"
    "<br>分析提示：%{customdata[14]}"
    "<br>官方更新时间：%{customdata[11]}"
    "<br>来源：%{customdata[12]}<extra></extra>"
)


def _base(theme: str, *, height: int = 390, kind: str = "") -> go.Figure:
    p = _palette(theme)
    fig = go.Figure()
    fig.update_layout(
        template="none", height=height, autosize=True,
        paper_bgcolor=p["paper"], plot_bgcolor=p["plot"],
        font={"family": "Inter, Segoe UI, Microsoft YaHei, sans-serif", "size": 12, "color": p["text"]},
        margin={"l": 22, "r": 24, "t": 22, "b": 60},
        legend={"orientation": "h", "y": -0.18, "x": 0, "font": {"size": 11}, "tracegroupgap": 4},
        hoverlabel={"bgcolor": p["hover"], "bordercolor": p["border"], "font": {"color": p["text"], "size": 12}},
        hovermode="closest", clickmode="event+select", dragmode="zoom",
        selectionrevision=True,
        meta={"chart_kind": kind, "source": "SQLite → Eurostat road_eqr_carpda", "missing_values": "kept as null"},
    )
    fig.update_xaxes(showgrid=False, zeroline=False, tickfont={"color": p["muted"]}, linecolor=p["border"], automargin=True)
    fig.update_yaxes(showgrid=True, gridcolor=p["grid"], zeroline=False, tickfont={"color": p["muted"]}, automargin=True)
    return fig


def _empty(fig: go.Figure, message: str = "当前筛选范围暂无可计算数据") -> go.Figure:
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    return fig


def _values(series: pd.Series) -> list[float | None]:
    return [None if pd.isna(value) else float(value) for value in series]


def trend_chart(frame: pd.DataFrame, mode: str = "absolute", base_year: int | None = None, theme: str = "dark") -> go.Figure:
    """同一种动力同色，同一个国家同线型；不会跨过缺失数据连线。"""
    if mode not in {"absolute", "yoy", "index"}:
        raise ValueError("趋势模式必须是 absolute、yoy 或 index")
    fig = _base(theme, height=400, kind=f"trend_{mode}")
    if frame.empty:
        return _empty(fig)
    p = _palette(theme)
    for (geo, energy), group in frame.sort_values("year").groupby(["geo", "mot_nrg"], sort=True):
        group = group.copy()
        if mode == "absolute":
            series = group["value"].where(group.get("eligible", pd.Series(True, index=group.index)))
        elif mode == "yoy":
            series = group["yoy_pct"]
        elif "index_value" in group:
            series = group["index_value"]
        else:
            # 后备逻辑只使用同一批次的真实基年；基年缺失或为零时不生成指数。
            baseline = group.loc[group["year"].eq(base_year)]
            valid = (
                not baseline.empty
                and pd.notna(baseline.iloc[0]["value"])
                and baseline.iloc[0]["value"] > 0
                and _is_true(baseline.iloc[0].get("eligible", True))
            )
            series = group["value"] / baseline.iloc[0]["value"] * 100 if valid else pd.Series(None, index=group.index, dtype="float64")
            series = series.where(group.get("eligible", pd.Series(True, index=group.index)))
        country = COUNTRY_NAMES.get(str(geo), str(group.iloc[0].get("country", geo)))
        symbols = ["star" if _is_true(row.get("is_max_change", False)) else "diamond" if _is_true(row.get("reversal")) else "diamond-open" if "i" in str(row.get("status", "")) else COUNTRY_SYMBOLS.get(str(geo), "circle") for row in group.to_dict("records")]
        fig.add_trace(go.Scatter(
            x=group["year"].tolist(), y=_values(series), mode="lines+markers", connectgaps=False,
            name=f"{country} · {POWERTRAIN_SHORT.get(str(energy), energy)}", legendgroup=str(energy),
            line={"color": POWERTRAIN_COLORS.get(str(energy), p["muted"]), "width": 2.5, "dash": COUNTRY_DASHES.get(str(geo), "solid")},
            marker={"size": [12 if symbol == "star" else 7 for symbol in symbols], "symbol": symbols, "line": {"width": 1}},
            customdata=_customdata(group), hovertemplate=HOVER,
            selected={"marker": {"size": 13, "opacity": 1}}, unselected={"marker": {"opacity": 0.6}},
        ))
    fig.update_xaxes(title_text="年份", dtick=1, tickformat="d")
    title = {"absolute": "年度注册量 · 辆", "yoy": "同比变化 · %", "index": f"注册量指数 · {base_year or '基年'} = 100"}[mode]
    fig.update_yaxes(title_text=title, tickformat=",.0f" if mode == "absolute" else ".1f", ticksuffix="%" if mode == "yoy" else "")
    if mode == "absolute":
        fig.update_yaxes(rangemode="tozero")
    elif mode == "yoy":
        fig.add_hline(y=0, line_color=p["border"], line_width=1)
    elif mode == "index":
        fig.add_hline(y=100, line_color=p["border"], line_dash="dot")
    return fig


def heatmap_chart(frame: pd.DataFrame, theme: str = "dark") -> go.Figure:
    """使用可点选的方形散点矩阵：灰色表示不可计算，绝不使用 0 代替。"""
    fig = _base(theme, height=410, kind="yoy_matrix")
    if frame.empty:
        return _empty(fig)
    p = _palette(theme)
    working = frame.sort_values(["geo", "mot_nrg", "year"]).copy()
    groups = [(geo, energy) for geo in sorted(working["geo"].unique()) for energy in (*POWERTRAIN_ORDER, 'PHEV_TOTAL', 'TOTAL') if ((working["geo"] == geo) & (working["mot_nrg"] == energy)).any()]
    # 每行保留约 50 像素。10 国单动力=10 行，不把 30 行挤进固定高度。
    fig.update_layout(height=max(410, 70 + len(groups) * 50))
    positions = {pair: i for i, pair in enumerate(groups)}
    working["matrix_y"] = [positions[(row.geo, row.mot_nrg)] for row in working.itertuples()]
    max_abs = max(1.0, float(working["yoy_pct"].abs().max())) if working["yoy_pct"].notna().any() else 1.0
    valid = working.loc[working["yoy_pct"].notna()]
    missing = working.loc[working["yoy_pct"].isna()]
    if not missing.empty:
        fig.add_trace(go.Scatter(
            x=missing["year"].tolist(), y=missing["matrix_y"].tolist(), mode="markers+text",
            marker={"symbol": "square", "size": 44, "color": p["gray"], "line": {"width": 1, "color": p["border"]}},
            text=["—"] * len(missing), textfont={"size": 13, "color": p["muted"]},
            customdata=_customdata(missing), hovertemplate=HOVER, showlegend=False,
        ))
    if not valid.empty:
        fig.add_trace(go.Scatter(
            x=valid["year"].tolist(), y=valid["matrix_y"].tolist(), mode="markers+text",
            marker={"symbol": "square", "size": 44, "color": valid["yoy_pct"].tolist(),
                    "colorscale": [[0, p["negative"]], [0.5, p["gray"]], [1, p["positive"]]],
                    "cmin": -max_abs, "cmax": max_abs, "cmid": 0, "showscale": True,
                    "colorbar": {"title": {"text": "%"}, "thickness": 9, "len": 0.82, "outlinewidth": 0},
                    "line": {"width": 1, "color": p["border"]}},
            text=[f"{value:+.1f}%" for value in valid["yoy_pct"]],
            textfont={"size": 10, "color": ["#102038" if abs(value) / max_abs > 0.45 else p["text"] for value in valid["yoy_pct"]]},
            customdata=_customdata(valid), hovertemplate=HOVER, showlegend=False,
            selected={"marker": {"opacity": 1}}, unselected={"marker": {"opacity": 0.6}},
        ))
    years = sorted(int(year) for year in working["year"].unique())
    fig.update_xaxes(tickmode="array", tickvals=years, ticktext=[str(year) for year in years], range=[min(years) - 0.6, max(years) + 0.6], title_text="年份")
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(groups))),
                     ticktext=[f"{COUNTRY_NAMES.get(str(geo), str(geo))} · {POWERTRAIN_SHORT.get(str(energy), str(energy))}" for geo, energy in groups],
                     range=[len(groups) - 0.5, -0.5], showgrid=False, title_text="")
    fig.update_layout(margin={"l": 15, "r": 35, "t": 18, "b": 50})
    return fig


def structure_chart(structure_frame: pd.DataFrame, mode: str = "share", theme: str = "dark") -> go.Figure:
    """分母是同国同年三类插电车型合计；不能解释为全动力新车渗透率。"""
    if mode not in {"share", "absolute"}:
        raise ValueError("结构模式必须是 share 或 absolute")
    fig = _base(theme, height=380, kind=f"structure_{mode}")
    if structure_frame.empty:
        return _empty(fig)
    p = _palette(theme)
    working = structure_frame.sort_values(["geo", "year", "mot_nrg"]).copy()
    for energy in POWERTRAIN_ORDER:
        group = working.loc[working["mot_nrg"].eq(energy)]
        if group.empty:
            continue
        series = group["share_pct"] if mode == "share" else group["value"]
        if "complete" in group:
            series = series.where(group["complete"].astype(bool))
        fig.add_trace(go.Bar(
            x=[f"{_country(row)} · {int(row['year'])}" for row in group.to_dict("records")],
            y=_values(series), name=POWERTRAIN_LABELS[energy], marker={"color": POWERTRAIN_COLORS[energy], "line": {"color": p["paper"], "width": 1}},
            customdata=_customdata(group),
            hovertemplate=HOVER if mode == "absolute" else HOVER.replace("<extra></extra>", "<br>计算三类内部占比：%{y:.2f}%<extra></extra>"),
            text=[None if pd.isna(value) else f"{float(value):.1f}%" if mode == "share" and value >= 7 else "" for value in series],
            textposition="inside", textfont={"color": "#132038", "size": 12},
            selected={"marker": {"opacity": 1}}, unselected={"marker": {"opacity": 0.55}},
        ))
    fig.update_layout(barmode="stack", bargap=0.42)
    fig.update_yaxes(title_text="三类内部构成 · %" if mode == "share" else "年度注册量 · 辆", rangemode="tozero", ticksuffix="%" if mode == "share" else "", tickformat=".0f" if mode == "share" else ",.0f")
    if mode == "share":
        fig.update_yaxes(range=[0, 104])
    if "complete" in working:
        incomplete = working.loc[~working["complete"].astype(bool)]
        for (_, year), group in incomplete.groupby(["geo", "year"]):
            row = group.iloc[0].to_dict()
            fig.add_annotation(x=f"{_country(row)} · {int(year)}", y=50 if mode == "share" else 0, text="数据不完整<br>无法计算结构", showarrow=False, font={"color": p["muted"], "size": 12})
    return fig


def quadrant_chart(quadrant_frame: pd.DataFrame, threshold: float | None, theme: str = "dark") -> go.Figure:
    """横轴是上年真实规模，纵轴是计算同比；象限只描述规模与增减。"""
    fig = _base(theme, height=390, kind="growth_quadrant")
    if quadrant_frame.empty:
        return _empty(fig, "暂无同批次、连续两年且基数非零的可比记录")
    p = _palette(theme)
    working = quadrant_frame.loc[quadrant_frame["previous_value"].notna() & quadrant_frame["yoy_pct"].notna()].copy()
    if working.empty:
        return _empty(fig)
    threshold = float(threshold) if threshold is not None and not pd.isna(threshold) else None
    upper = max(15, float(working["yoy_pct"].max()) * 1.22)
    lower = min(-15, float(working["yoy_pct"].min()) * 1.22)
    xmax = max(float(working["previous_value"].max()) * 1.25, float(threshold or 0) * 1.2, 1)
    if threshold is not None and not pd.isna(threshold):
        # 背景表示分类区域，不表示机会评分或政策判断。
        for x0, x1, y0, y1, positive in [(0, threshold, 0, upper, True), (threshold, xmax, 0, upper, True), (0, threshold, lower, 0, False), (threshold, xmax, lower, 0, False)]:
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=p["positive"] if positive else p["negative"], opacity=0.04, line_width=0, layer="below")
        fig.add_vline(x=float(threshold), line_color=p["border"], line_dash="dash")
    fig.add_hline(y=0, line_color=p["border"], line_width=1)
    for (geo, energy), group in working.groupby(["geo", "mot_nrg"]):
        fig.add_trace(go.Scatter(
            x=group["previous_value"].tolist(), y=group["yoy_pct"].tolist(), mode="markers+text",
            name=f"{COUNTRY_NAMES.get(str(geo), str(geo))} · {POWERTRAIN_SHORT.get(str(energy), str(energy))}",
            text=[f"{geo} · {POWERTRAIN_SHORT.get(str(energy), str(energy))}"] * len(group), textposition="top center",
            textfont={"color": p["text"], "size": 10},
            marker={"color": POWERTRAIN_COLORS.get(str(energy), p["muted"]), "size": 13, "symbol": COUNTRY_SYMBOLS.get(str(geo), "circle"), "line": {"width": 1.5, "color": p["paper"]}},
            customdata=_customdata(group), hovertemplate=HOVER,
            selected={"marker": {"size": 18, "opacity": 1}}, unselected={"marker": {"opacity": 0.55}},
            showlegend=False,
        ))
    labels = [(0.02, 0.98, "较小规模 · 扩张"), (0.98, 0.98, "较大规模 · 扩张"), (0.02, 0.02, "较小规模 · 收缩"), (0.98, 0.02, "较大规模 · 收缩")]
    if threshold is not None:
        for x, y, label in labels:
            fig.add_annotation(x=x, y=y, xref="paper", yref="paper", text=label, showarrow=False, xanchor="left" if x < 0.5 else "right", yanchor="top" if y > 0.5 else "bottom", font={"size": 11, "color": p["muted"]})
    fig.update_xaxes(title_text="上一年注册规模 · 辆", range=[0, xmax], tickformat=",.0f")
    fig.update_yaxes(title_text="当年同比 · %", range=[lower, upper], ticksuffix="%", tickformat=".0f")
    fig.update_layout(margin={"l": 22, "r": 25, "t": 24, "b": 65})
    return fig


def europe_map(frame: pd.DataFrame, metric: str = "value", theme: str = "dark") -> go.Figure:
    """只给已采集、有可用数值的国家着色；灰色边界没有市场数值。

    离线地图需调用者设置 config.topojsonURL='/app/static/maps/'，
    项目启动后由 Streamlit 从本地目录提供欧洲真实地理底图。
    """
    if metric not in {"value", "yoy_pct", "delta"}:
        raise ValueError("地图指标必须是 value、yoy_pct 或 delta")
    fig = _base(theme, height=430, kind=f"europe_map_{metric}")
    p = _palette(theme)
    fig.update_geos(
        scope="europe", resolution=110, projection_type="natural earth",
        showland=True, landcolor=p["gray"], showcountries=True, countrycolor=p["border"], countrywidth=0.7,
        showcoastlines=True, coastlinecolor=p["border"], coastlinewidth=0.5,
        showocean=True, oceancolor=p["ocean"], showlakes=False, bgcolor=p["paper"],
        lonaxis_range=[-15, 38], lataxis_range=[34, 72],
    )
    fig.update_layout(margin={"l": 0, "r": 16, "t": 5, "b": 45}, dragmode="pan")
    fig.add_annotation(x=0, y=-0.05, xref="paper", yref="paper", text="灰色：暂无数据，或筛选后分项不完整", xanchor="left", showarrow=False, font={"color": p["muted"], "size": 11})
    if frame.empty:
        return _empty(fig, "当前范围暂无可映射的真实数据")
    if frame["geo"].duplicated().any():
        raise ValueError("地图输入必须是单一分析年、每个国家一行的聚合结果，避免重复着色")
    working = frame.loc[frame[metric].notna()].copy()
    if "eligible" in working:
        working = working.loc[working["eligible"].astype(bool)]
    if "complete" in working:
        working = working.loc[working["complete"].astype(bool)]
    working["iso3"] = working["geo"].map(EUROPE_ISO3)
    working = working.loc[working["iso3"].notna()]
    if working.empty:
        return _empty(fig, "当前筛选没有完整、可计算的国家记录")
    positive = metric == "value"
    maximum = max(1.0, float(working[metric].abs().max()))
    if positive:
        color_scale = [
            [0, "#183550" if theme == "dark" else "#DBEEF7"],
            [0.5, "#26779A"],
            [1, "#56D4E5"],
        ]
    else:
        color_scale = [[0, p["negative"]], [0.5, p["gray"]], [1, p["positive"]]]
    fig.add_trace(go.Choropleth(
        locations=working["iso3"].tolist(), locationmode="ISO-3", z=working[metric].tolist(),
        zmin=0 if positive else -maximum, zmax=maximum, zmid=None if positive else 0,
        colorscale=color_scale, autocolorscale=False,
        marker={"line": {"color": p["border"], "width": 1}},
        colorbar={"title": {"text": {"value": "辆", "yoy_pct": "%", "delta": "净增 · 辆"}[metric]}, "thickness": 10, "len": 0.75, "outlinewidth": 0, "tickformat": ".1f" if metric == "yoy_pct" else ",.0f"},
        customdata=_customdata(working), hovertemplate=HOVER,
        selected={"marker": {"opacity": 1}}, unselected={"marker": {"opacity": 0.6}},
        showscale=True,
    ))
    return fig


def ranking_chart(rankings: pd.DataFrame, metric: str = "bev_value", theme: str = "dark") -> go.Figure:
    """国家排名只使用可计算指标；并列排名由分析层确定，缺失项不填零。"""
    metrics = {"bev_value": ("纯电注册量 · 辆", "registrations_rank"),
               "yoy_pct": ("纯电同比 · %", "yoy_rank"),
               "delta": ("纯电年度净增 · 辆", "delta_rank"),
               "bev_share_pct": ("BEV 占全部新车 · %", "share_rank")}
    if metric not in metrics:
        raise ValueError("国家排名指标不支持")
    fig = _base(theme, height=max(350, 90 + len(rankings) * 36), kind=f"country_ranking_{metric}")
    if rankings.empty:
        return _empty(fig, "当前选择暂无国家排名数据")
    title, rank_column = metrics[metric]
    working = rankings.loc[rankings[metric].notna()].sort_values([metric, "geo"], ascending=[False, True]).copy()
    if working.empty:
        return _empty(fig, "当前指标没有完整、可比的国家数据，未生成排名")
    p = _palette(theme)
    working["mot_nrg"] = "ELC"
    working["value"] = working["bev_value"]
    working["previous_value"] = working["bev_value"] - working["delta"]
    pct = metric in {"yoy_pct", "bev_share_pct"}
    labels = [f"{int(row[rank_column]) if pd.notna(row.get(rank_column)) else '—'} · {_country(row)}" for row in working.to_dict("records")]
    colors = [p["negative"] if value < 0 else p["positive"] for value in working[metric]] if metric in {"yoy_pct", "delta"} else POWERTRAIN_COLORS["ELC"]
    custom = _customdata(working)
    for details, row in zip(custom, working.to_dict("records")):
        details.extend([_plain(row.get("total_status"), "无"), _plain(row.get("total_source_url")),
                        CALCULATION_STATUS.get(row.get("share_status"), _plain(row.get("share_status"))),
                        _number(row.get("total_value"))])
    fig.add_trace(go.Bar(
        x=_values(working[metric]), y=labels, orientation="h",
        marker={"color": colors}, customdata=custom,
        hovertemplate=HOVER.replace("<extra></extra>", "<br>" + title + ("：%{x:.2f}%" if pct else "：%{x:,.0f}")
            + "<br>真实 TOTAL 分母：%{customdata[19]} 辆 · 分母标记：%{customdata[16]}"
            + "<br>占比状态：%{customdata[18]}<extra></extra>"),
        text=[f"{float(value):.2f}%" if metric == "bev_share_pct" else _number(value, pct=pct, signed=metric == "delta") for value in working[metric]],
        textposition="outside", cliponaxis=False, showlegend=False,
        selected={"marker": {"opacity": 1}}, unselected={"marker": {"opacity": 0.55}},
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(title_text=title, showgrid=True, gridcolor=p["grid"], ticksuffix="%" if pct else "", tickformat=".1f" if pct else ",.0f")
    fig.update_layout(margin={"l": 15, "r": 75, "t": 15, "b": 55})
    return fig
