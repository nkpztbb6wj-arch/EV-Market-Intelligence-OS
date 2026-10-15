"""离线研究终端入口：三类注册量与 TOTAL 分母分开读取，避免统计口径混算。"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

from evmi import analytics, charts, data, reporting, ui, dft, unified, public_mode
from evmi.markets import EU_COUNTRIES, TARGET_COUNTRIES

st.set_page_config(page_title="EV Market Intelligence OS", page_icon="◈", layout="wide")


def evidence_fingerprint():
    # 数据库或来源文件发生变化时失效缓存；刷新按钮也可主动清除。
    paths = [data.DEFAULT_DB]
    for pattern in ("outputs/*/manifest.json", "outputs/*/validation_report.json",
                    "data/raw/*/*.json", "data/processed/*/*.csv",
                    "outputs/phase4/uk_batches/*/manifest.json",
                    "outputs/phase4/dft_research/*/raw/*.ods"):
        paths.extend(data.ROOT.glob(pattern))
    return tuple((str(path.relative_to(data.ROOT)), path.stat().st_mtime_ns, path.stat().st_size)
                 for path in sorted(paths) if path.is_file())


@st.cache_data(ttl=30, show_spinner=False)
def verified_batches(fingerprint):
    return data.list_verified_batches()


@st.cache_data(ttl=30, show_spinner=False)
def batch_data(batch_id, fingerprint):
    return data.load_batch(batch_id)


@st.cache_data(ttl=30, show_spinner=False)
def batch_totals(batch_id, fingerprint):
    """旧快照没有 TOTAL 时返回空表，不能向历史批次借用新分母。"""
    return data.load_totals(batch_id)


@st.cache_data(ttl=30, show_spinner=False)
def uk_data(batch_id, fingerprint):
    return dft.load_batch(batch_id)


def clear_focus():
    st.session_state["focus"] = []
    st.session_state["selection_epoch"] = st.session_state.get("selection_epoch", 0) + 1


def reset_filters(countries, energies, years):
    st.session_state["country_group"] = "全部"
    st.session_state["countries"] = countries
    st.session_state["energies"] = energies
    st.session_state["year_range"] = (min(years), max(years))
    st.session_state["analysis_year"] = max(years)
    st.session_state["exclude_imputed"] = False
    clear_focus()


def apply_focus():
    points = st.session_state.get("focus", [])
    if points:
        st.session_state["country_group"] = "自定义"
        st.session_state["countries"] = sorted({point[0] for point in points})
        st.session_state["energies"] = sorted({point[1] for point in points if point[1] in unified.DISPLAY_CODES})
        st.session_state["analysis_year"] = max(point[2] for point in points)
    clear_focus()


def change_country_group(available):
    """分组只选择当前真实批次里的国家；目标英国没有数据时不会偷偷进入汇总。"""
    group = st.session_state.get("country_group", "全部")
    if group != "自定义":
        st.session_state["countries"] = [code for code in available
            if group == "全部" or (group == "欧盟" and code in EU_COUNTRIES)
            or (group == "非欧盟" and code not in EU_COUNTRIES)]
    clear_focus()


def change_countries():
    st.session_state["country_group"] = "自定义"
    clear_focus()


@st.fragment(run_every="2s")
def synchronize_theme():
    """原生主题改变不会自动重跑 Python；小片段同步自有卡片与 Plotly 配色。"""
    if st.session_state.get("rendered_theme") != ui.theme_name():
        st.rerun(scope="app")


def show_chart(figure, name, context, selectable=True):
    if context.get('mixed_sources'):
        # 说明写入图本身，下载 PNG 后仍可看到来源范围，而不只留在网页旁边。
        figure.add_annotation(x=0, y=1.10, xref='paper', yref='paper', xanchor='left', showarrow=False,
            text='Eurostat / DfT-DVLA · 英国含进口二手车 · TOTAL 为约数 · 并列展示，非严格排名', font={'size':10})
        figure.add_annotation(x=0, y=1.18, xref='paper', yref='paper', xanchor='left', showarrow=False,
            text=dft.ATTRIBUTION, font={'size':9})
        figure.update_layout(margin_t=85)
    config = {"displaylogo": False, "scrollZoom": False, "responsive": True,
              "topojsonURL": "/app/static/maps/",
              "toImageButtonOptions": {"format": "png", "filename": f"evmi_{name}_{context['year']}",
                                       "scale": 2, "width": 1400, "height": max(650, figure.layout.height or 650)}}
    key = f"chart_{name}_{context['batch']}_{st.session_state.get('selection_epoch',0)}"
    event = st.plotly_chart(figure, key=key, width="stretch", config=config,
                           on_select="rerun" if selectable else "ignore",
                           selection_mode=("points", "box", "lasso"),
                           theme=None, alt=f"{name}：真实官方注册数据及其计算结果")
    if selectable:
        points = event.selection.points
        keys = []
        for point in points:
            custom = point.get("customdata", [])
            if len(custom) >= 3 and custom[1] == 'SELECTED':
                for energy in context.get('energies', analytics.ENERGIES):
                    item = (str(custom[0]), energy, int(custom[2]))
                    if item not in keys:
                        keys.append(item)
            if len(custom) >= 3 and custom[1] in unified.DISPLAY_CODES:
                item = (str(custom[0]), str(custom[1]), int(custom[2]))
                if item not in keys:
                    keys.append(item)
        if keys and keys != st.session_state.get("focus", []):
            st.session_state["focus"] = keys
            st.rerun()


def main():
    theme = ui.theme_name()
    st.session_state["rendered_theme"] = theme
    ui.inject_style(theme)
    if public_mode.enabled():
        from evmi.portfolio import landing
        if landing():
            return
    with st.expander('AI Market Research · 市场分析报告', expanded=public_mode.enabled() and st.session_state.get('ai_workspace', False)):
        if st.checkbox('打开报告工作区', key='ai_workspace'):
            from evmi.ai_report_ui import render as render_ai_workspace
            render_ai_workspace()
    signature = evidence_fingerprint()
    try:
        batches, warnings = verified_batches(signature)
    except (OSError, ValueError) as exc:
        st.error(f"无法读取本地已验证数据库：{exc}")
        st.stop()
    if not batches:
        st.error("暂无通过来源验证的批次。请保留原始 JSON、CSV 和验证报告，并检查数据库。")
        for warning in warnings:
            st.warning(warning)
        st.stop()
    with st.sidebar:
        st.html('<div class="evmi-sidebar-brand">EV / INTELLIGENCE</div>')
        st.caption("欧洲市场研究 · 本地离线数据")
        batch_ids = [item["batch_id"] for item in batches]
        if st.session_state.get("batch_id") not in batch_ids:
            st.session_state["batch_id"] = batch_ids[0]
        metadata = {item["batch_id"]: item for item in batches}
        def batch_label(batch_id):
            when = datetime.fromisoformat(metadata[batch_id]["retrieved_at_utc"]).astimezone(ZoneInfo("Asia/Shanghai"))
            return f"{when:%Y-%m-%d %H:%M} · 已验证"
        batch_id = st.selectbox("采集批次", batch_ids, format_func=batch_label, key="batch_id", on_change=clear_focus)
        st.caption(f"{len(batches)} 个有效历史快照 · 默认最新采集")
        if st.button("刷新本地数据", width="stretch"):
            verified_batches.clear()
            batch_data.clear()
            batch_totals.clear()
            uk_data.clear()
            clear_focus()
            st.rerun()
    batch, raw = batch_data(batch_id, signature)
    totals = batch_totals(batch_id, signature)
    euro_raw, euro_totals = raw, totals
    uk_metadata, uk_original = None, pd.DataFrame()
    uk_batches = dft.list_batches()
    # 来源批次分别选择；历史 Eurostat 默认独立展示，不自动拼接最新英国快照。
    with st.sidebar:
        analysis_mode = st.radio("分析模式", unified.MODES, key="analysis_mode", on_change=clear_focus)
        uk_options = ["NONE", *[b["batch_id"] for b in uk_batches]]
        if st.session_state.get("uk_scope_euro_batch") != batch_id:
            st.session_state["uk_batch_id"] = uk_options[1] if batch_id == batch_ids[0] and len(uk_options) > 1 else "NONE"
            st.session_state["uk_scope_euro_batch"] = batch_id
        if st.session_state.get("uk_batch_id") not in uk_options:
            st.session_state["uk_batch_id"] = "NONE"
        uk_id = st.selectbox("英国 DfT 来源批次", uk_options, key="uk_batch_id",
            format_func=lambda value: "不叠加英国快照" if value == "NONE" else value,
            on_change=clear_focus, help="两个来源有各自的采集时间；仅并列展示，绝不当作同批采集或相加。")
    if uk_id != "NONE":
        try:
            uk_metadata, uk_original = uk_data(uk_id, signature)
        except (ValueError, OSError) as exc:
            st.warning(f"英国来源验证失败，保留 Eurostat 界面：{exc}")
    raw = unified.eurostat_frame(euro_raw)
    totals = unified.eurostat_frame(euro_totals)
    if uk_metadata is not None:
        uk_frame = dft.display_frame(uk_metadata, uk_original)
        raw = pd.concat([raw, uk_frame.loc[uk_frame.mot_nrg.isin(analytics.ENERGIES)]], ignore_index=True)
        totals = pd.concat([totals, uk_frame.loc[uk_frame.mot_nrg.eq('TOTAL')]], ignore_index=True)
    available_raw = raw
    # 严格模式的过滤发生在计算入口，不只是在图上隐藏英国。
    if analysis_mode == unified.STRICT:
        raw = unified.strict_sample(raw, 'value')
        totals = unified.strict_sample(totals, 'total')
    if uk_metadata is not None:
        raw = unified.extra_series(raw, totals)
    all_countries = sorted(raw.geo.unique())
    country_options = list(dict.fromkeys([*TARGET_COUNTRIES, *all_countries]))
    country_labels = raw.drop_duplicates("geo").set_index("geo").country.to_dict()
    all_energies = [code for code in unified.DISPLAY_CODES if code in set(raw.mot_nrg)]
    energy_names = {**analytics.ENERGY_NAMES, **unified.EXTRA_NAMES}
    years = sorted(int(year) for year in raw.year.unique())
    # 新批次国家/年份可能变化，只修正已无效的选择，不混入虚构项目。
    if "countries" in st.session_state:
        valid_countries = [code for code in st.session_state["countries"] if code in country_options]
        if valid_countries != st.session_state["countries"]:
            st.session_state["countries"] = valid_countries
    if "analysis_year" in st.session_state and st.session_state["analysis_year"] not in years:
        st.session_state["analysis_year"] = max(years)
    if "year_range" in st.session_state and any(year not in years for year in st.session_state["year_range"]):
        st.session_state["year_range"] = (min(years), max(years))
    with st.sidebar:
        st.divider()
        st.radio("国家分组", ["全部", "欧盟", "非欧盟", "自定义"], horizontal=True,
                 key="country_group", on_change=change_country_group, args=(all_countries,))
        requested_countries = st.multiselect("国家", country_options, default=None if "countries" in st.session_state else all_countries,
                                  format_func=lambda code: ui.COUNTRY_NAMES.get(code, country_labels.get(code, code)) +
                                      ('（口径不可比）' if code=='GB' and uk_metadata and analysis_mode==unified.STRICT
                                       else "（暂无数据）" if code not in all_countries else ""),
                                  key="countries", on_change=change_countries)
        st.caption(f"当前分析模式可展示 {len(all_countries)} / {len(TARGET_COUNTRIES)} 个目标国家。")
        if uk_metadata:
            st.caption("英国 DfT/DVLA 独立批次 · 含进口二手车；严格排名暂不纳入。")
        if 'energies' in st.session_state:
            st.session_state['energies'] = [e for e in st.session_state['energies'] if e in all_energies]
        energies = st.multiselect("动力类型", all_energies, default=None if "energies" in st.session_state else list(analytics.ENERGIES),
                                 format_func=lambda code: energy_names[code], key="energies", on_change=clear_focus)
        effective = unified.selection_codes(energies)
        if effective != energies:
            st.caption('防止重复统计：TOTAL 选择时单独展示；PHEV 合计选择时替代两个分项。')
        energies = effective
        selected_range = st.select_slider("趋势展示范围", options=years, value=(min(years), max(years)),
                                          key="year_range", on_change=clear_focus)
        year = st.selectbox("分析年份", years, index=len(years)-1, key="analysis_year", on_change=clear_focus,
                            help="指标、地图、结构和四象限使用这个年份；趋势展示范围独立控制。")
        exclude = st.toggle("排除官方插补值", key="exclude_imputed", on_change=clear_focus,
                            help="保留原始记录；仅在分析中排除含 i 的值，可能导致结构和同比不可计算。")
        st.button("恢复默认筛选", on_click=reset_filters, args=(all_countries, list(analytics.ENERGIES), years), width="stretch")
        st.divider()
        st.caption("主题：右上角 ⋮ → Dark / Light；图表约 2 秒内同步。")
        st.caption("A = 年度；英国 TOTAL 原单位千辆，换算辆数仍为约数。注册量不是销量或保有量。")
        st.link_button("Eurostat 官方数据集", batch["source_page"], width="stretch")
    # 没有真实观察的目标国家可被查看，但不进入合计、排名或占比分母。
    countries = [code for code in requested_countries if code in all_countries]
    unavailable = [code for code in requested_countries if code not in all_countries]
    ui.header(batch, year, countries, energies, raw)
    if uk_metadata:
        st.info(unified.NOTICE)
        st.caption(f"Eurostat：{batch_id}；DfT/DVLA：{uk_id}（独立来源快照并列显示）。")
    if unavailable:
        st.warning("、".join(ui.COUNTRY_NAMES.get(code, code) for code in unavailable) + " 暂无可比真实数据，未纳入任何合计、排名或占比。英国可在概览模式查看已验证 DfT 数据。")
    if not countries or not energies:
        st.info("请选择至少一个国家和一种动力类型，再开始分析。")
        st.stop()
    for warning in warnings:
        st.warning(warning)
    prepared = analytics.prepare_observations(raw, exclude_imputed=exclude)
    euro_prepared = analytics.prepare_observations(unified.eurostat_frame(euro_raw), exclude_imputed=exclude)
    overview_active = uk_metadata is not None and analysis_mode == unified.OVERVIEW
    if overview_active:
        if st.session_state.get('kpi_country') not in countries:
            st.session_state['kpi_country'] = countries[0]
        kpi_country = st.selectbox('指标焦点国家', countries, key='kpi_country', format_func=lambda c: ui.COUNTRY_NAMES.get(c,c))
        st.caption('概览核心卡片显示单国指标；选择一个国家时自动跟随。多国曲线与地图继续使用全局国家筛选。')
        metric_countries = [kpi_country]
    else:
        metric_countries = countries
    metric_prepared = prepared.loc[prepared.geo.isin(metric_countries)]
    displayed_years = [value for value in years if selected_range[0] <= value <= selected_range[1]]
    displayed = analytics.filter_observations(prepared, countries, energies, displayed_years)
    aggregate = unified.national_aggregate(metric_prepared, metric_countries, energies, year)
    full_structure = unified.national_aggregate(metric_prepared, metric_countries, analytics.ENERGIES, year)
    bev = unified.national_aggregate(metric_prepared, metric_countries, ["ELC"], year)
    bev_share = (100.0 * bev["value"] / full_structure["value"]
                 if full_structure["complete"] and full_structure["value"] and bev["complete"] else None)
    context = {"year": year, "batch": batch_id, 'mixed_sources': overview_active, 'energies': energies}
    market_share = (unified.national_share(prepared, totals, metric_countries[0], year, exclude)
                    if overview_active else analytics.market_share(prepared, totals, countries, year, exclude_imputed=exclude))
    rank_countries = [c for c in countries if c in set(euro_raw.geo)]
    rankings = analytics.country_rankings(euro_prepared, euro_totals, rank_countries, year, exclude_imputed=exclude)
    if overview_active:
        rankings = rankings.iloc[:0].copy()
    ui.metric_cards(aggregate, bev_share, aggregate["imputed_count"], year, len(metric_countries), market_share=market_share,
                    country=metric_countries[0] if overview_active else None)
    st.caption(f"所选动力当年可用组合 {aggregate['available_count']}/{aggregate['expected_count']}；"
               f"BEV/TOTAL 可比国家 {market_share['available_count']}/{market_share['expected_count']}。"
               "这是观察覆盖率，不是欧洲市场覆盖份额。英国不参与跨来源合计。")
    if not market_share["complete"]:
        st.caption("全新车占比说明：" + ui.REASONS.get(market_share["reason"], market_share["reason"]))
    if not aggregate["complete"] or not aggregate["previous_complete"]:
        st.caption(f"比较说明：{ui.REASONS.get(aggregate['reason'],aggregate['reason'])}；"
                   f"当年可用 {aggregate['available_count']}/{aggregate['expected_count']} 个分项。空值不表示零。")
    overview, ranking_tab, brief, evidence, roadmap = st.tabs(["市场研究终端", "国家排名与覆盖", "商业汇报摘要", "数据与来源", "扩展路线"])
    with overview:
        displayed_flags = sum("i" in str(status) for status in displayed.status)
        st.caption(f"当前展示范围的原始观察含 {displayed_flags} 条官方插补标记；"
                   + ("这些值保留在明细中，但不参与当前计算。" if exclude else "比较结果保留标记，可在悬浮和明细中追溯。"))
        if st.session_state.get("focus"):
            st.caption("图表选择已定位到下方证据明细。可将该选择应用为全局国家、动力与分析年份筛选。")
            a, b, _ = st.columns([1, 1, 3])
            a.button("应用图表选择为筛选", on_click=apply_focus, width="stretch")
            b.button("清除图表选择", on_click=clear_focus, width="stretch")
        # 原生水平容器自动换行：1920桌面两列，普通笔记本足够宽的单列图。
        row = st.container(horizontal=True, wrap=True, gap=20)
        left = row.container(border=True, width=780)
        right = row.container(border=True, width=630)
        with left:
            ui.panel_title("年度注册趋势", "真实数量 / 计算结果 · 国家与动力分开比较")
            mode_label = st.radio("趋势视角", ["注册量", "同比增长率", "基年指数"], horizontal=True, on_change=clear_focus)
            mode = {"注册量": "absolute", "同比增长率": "yoy", "基年指数": "index"}[mode_label]
            base_year = years[0]
            trend_frame = displayed
            if mode == "index":
                base_year = st.selectbox("指数基年", years, key="base_year")
                trend_frame = analytics.filter_observations(analytics.index_trends(prepared, base_year), countries, energies, displayed_years)
            if len(countries) > 3:
                # 十国同时显示三类会形成 30 条线，动力焦点不改动顶部全局汇总。
                options = list(energies) + ["ALL"]
                if st.session_state.get("trend_energy") not in options:
                    st.session_state["trend_energy"] = options[0]
                focus_energy = st.selectbox("趋势动力焦点", options, key="trend_energy",
                                           format_func=lambda code: "全部动力" if code == "ALL" else energy_names[code], on_change=clear_focus)
                if focus_energy != "ALL":
                    trend_frame = trend_frame.loc[trend_frame.mot_nrg.eq(focus_energy)]
                st.caption("趋势动力焦点仅控制本图；国家由线型与标记区分，动力颜色保持一致。")
            show_chart(charts.trend_chart(trend_frame, mode=mode, base_year=base_year, theme=theme), "trend", context)
            st.caption("★ = 每国×动力系列本批次可比年份的最大绝对净变动；悬浮显示两年数量和官方标记。")
        with right:
            ui.panel_title("同比增长热力矩阵", "计算结果 · 0% 为中点 · 灰色格子表示不可计算")
            heatmap_frame = displayed
            if len(countries) > 3:
                if st.session_state.get("heatmap_energy") not in energies:
                    st.session_state["heatmap_energy"] = energies[0]
                heatmap_energy = st.selectbox("热力矩阵动力", energies, key="heatmap_energy", format_func=lambda code: energy_names[code], on_change=clear_focus)
                heatmap_frame = displayed.loc[displayed.mot_nrg.eq(heatmap_energy)]
            show_chart(charts.heatmap_chart(heatmap_frame, theme=theme), "heatmap", context)
            st.caption("可点选格子定位明细。2022 年没有本批次内的上一年，不填为 0%。")
            st.caption("真实注册量为零仍保留为零；上一年为零时同比不可计算，显示灰色并说明零分母。")
        row = st.container(horizontal=True, wrap=True, gap=20)
        left = row.container(border=True, width=780)
        right = row.container(border=True, width=630)
        with left:
            ui.panel_title("三类动力内部构成", "固定三类分母 · 不是全新车渗透率")
            structure_mode = st.radio("结构视角", ["内部占比", "绝对数量"], horizontal=True)
            structure = unified.structures(prepared, countries)
            structure = structure.loc[structure.year.isin(displayed_years)]
            if len(countries) > 3:
                structure_scope = st.radio("结构比较范围", ["分析年份跨国", "单国年度趋势"], horizontal=True)
                if structure_scope == "分析年份跨国":
                    structure = unified.structures(prepared, countries)
                    structure = structure.loc[structure.year.eq(year)]
                else:
                    structure_country = st.selectbox("结构国家", countries, format_func=lambda code: ui.COUNTRY_NAMES.get(code, code))
                    structure = structure.loc[structure.geo.eq(structure_country)]
            show_chart(charts.structure_chart(structure, mode="share" if structure_mode == "内部占比" else "absolute", theme=theme), "structure", context)
            st.caption("结构保持三类完整分母，动力筛选不改变该图分母；缺失或排除分项的年份不生成完整结构。")
        with right:
            ui.panel_title("规模 × 增长四象限", f"计算结果 · {year} 年 · 规模门槛固定于本批次完整可比样本")
            quadrants, threshold = analytics.quadrant_data(euro_prepared, year)
            if overview_active:
                quadrants = prepared.loc[prepared.year.eq(year) & prepared.mot_nrg.isin(analytics.ENERGIES)
                    & prepared.eligible & prepared.yoy_pct.notna()].copy()
                threshold = None
                st.caption('概览仅并列展示各国规模与增长散点；跨来源不划分统一高低规模象限。')
            focus_quadrants = quadrants.loc[quadrants.geo.isin(countries) & quadrants.mot_nrg.isin(energies)]
            show_chart(charts.quadrant_chart(focus_quadrants, threshold, theme=theme), "quadrants", context)
            st.caption(f"上一年规模中位数：{threshold:,.1f} 辆；增长分界 0%。" if threshold is not None else "完整可比样本不足，展示散点而不强行划分规模象限。")
            full_annual = prepared.loc[prepared.year.eq(year) & prepared.mot_nrg.isin(analytics.ENERGIES)]
            zero_bases = int(full_annual.yoy_status.eq("previous_zero").sum())
            st.caption(f"原始三类样本可比 {len(quadrants)}/{len(all_countries) * len(analytics.ENERGIES)}；"
                       f"上年零分母 {zero_bases} 条。门槛仍要求完整国家×三类样本，不把 29/30 偷改为完整样本。")
        row = st.container(horizontal=True, wrap=True, gap=20)
        left = row.container(border=True, width=780)
        right = row.container(border=True, width=630)
        with left:
            ui.panel_title("欧洲已覆盖市场地图", "本地真实地理底图 · 其他国家暂无数据")
            map_metric_label = st.radio("地图指标", ["注册量", "同比", "绝对净增"], horizontal=True)
            metric = {"注册量": "value", "同比": "yoy_pct", "绝对净增": "delta"}[map_metric_label]
            map_rows = []
            for country in countries:
                summary = unified.national_aggregate(prepared.loc[prepared.geo.eq(country)], [country], energies, year)
                national = prepared.loc[prepared.geo.eq(country) & prepared.year.eq(year) & prepared.mot_nrg.isin(energies)]
                previous_national = prepared.loc[prepared.geo.eq(country) & prepared.year.eq(year-1) & prepared.mot_nrg.isin(energies)]
                map_rows.append({"geo": country, "country": country_labels[country], "year": year,
                                 "mot_nrg": energies[0] if len(energies) == 1 else "SELECTED",
                                 "value": summary["value"], "previous_value": summary["previous_value"],
                                 "delta": summary["delta"], "yoy_pct": summary["yoy_pct"],
                                 "status": "i" if summary["imputed_count"] else "", "yoy_status": summary["reason"],
                                 "previous_status": "i" if previous_national.status.str.contains("i",regex=False).any() else "",
                                 "complete": summary["complete"],
                                 "dataset": national.dataset.iloc[0] if not national.empty else '',
                                 "scope_note": national.scope_note.iloc[0] if not national.empty else '',
                                 "value_kind": national.value_kind.iloc[0] if not national.empty else '',
                                 "source_updated": national.source_updated.iloc[0] if not national.empty else '',
                                 "source_url": national.source_url.iloc[0] if not national.empty else ''})
            show_chart(charts.europe_map(pd.DataFrame(map_rows), metric=metric, theme=theme), "map", context, selectable=True)
            st.caption("地图跟随全局筛选；用侧栏国家选择定位市场。灰色不表示零，未采集国家不会生成数值。")
        with right:
            ui.panel_title("年度变化观察", "分析提示 · 自动识别增长、下降、反转和最大变化")
            facts = unified.insights(prepared, countries, energies, year)
            if not facts:
                st.info("当前分析年没有可比变化；请选择具备上一年数据的年份或检查质量筛选。")
            for fact in facts[:5]:
                ui.insight(fact["text"])
            if len(facts) > 5:
                with st.expander(f"查看全部 {len(facts)} 条年度变化提示"):
                    for fact in facts:
                        ui.insight(fact["text"])
            st.caption("提示只描述数据变化，不推断补贴政策、竞争或其他商业原因。")
        with st.container(border=True):
            ui.panel_title("证据明细与导出", "原始字段保留 · 可追溯计算 · PNG 可由每张图右上角相机导出")
            detail = displayed
            selected_points = st.session_state.get("focus", [])
            if selected_points:
                detail = prepared.loc[prepared.apply(lambda row: (row.geo, row.mot_nrg, int(row.year)) in selected_points, axis=1)]
                st.caption(f"正在显示图表选中的 {len(detail)} 条观察；清除选择可返回当前范围。")
            st.dataframe(detail[["geo", "year", "powertrain_zh", "value", "previous_value", "delta", "yoy_pct", "yoy_status", "status", "reversal", "is_max_change", "source_id", "scope_note", "value_kind"]],
                         hide_index=True, width="stretch", column_config={"yoy_pct": st.column_config.NumberColumn("同比（%）", format="%.2f"),
                             "value": "真实注册量（辆）", "delta": "计算净增（辆）", "status": "官方标记"})
            a, b = st.columns(2)
            a.download_button("导出原始与派生明细 CSV" if uk_metadata else "导出原始观察 CSV", unified.export_csv(detail) if uk_metadata else detail[list(data.ROW_COLUMNS)].to_csv(index=False).encode("utf-8-sig"),
                              file_name=f"registrations_{batch_id}.csv", mime="text/csv", width="stretch")
            b.download_button("导出计算结果 CSV", detail.to_csv(index=False).encode("utf-8-sig"),
                              file_name=f"analysis_{batch_id}.csv", mime="text/csv", width="stretch")
    with ranking_tab:
        ui.panel_title(f"{year} 年国家 BEV 排名", "固定纯电口径 · 真实规模 / 计算增长 / 全新车占比分开比较")
        if overview_active:
            st.info('概览模式不生成国家排名。切换「严格可比分析」后，按指标审批规则显示 Eurostat 可比样本。')
        rank_modes = {"纯电注册量": ("bev_value", "registrations_rank"), "纯电同比增长率": ("yoy_pct", "yoy_rank"),
                      "纯电绝对净增": ("delta", "delta_rank"), "BEV 全新车占比": ("bev_share_pct", "share_rank")}
        rank_label = st.radio("排名指标", list(rank_modes), horizontal=True, on_change=clear_focus)
        rank_metric, rank_column = rank_modes[rank_label]
        show_chart(charts.ranking_chart(rankings, metric=rank_metric, theme=theme), "country_rankings", context)
        available_ranks = int(rankings[rank_metric].notna().sum())
        st.caption(f"本项有效国家 {available_ranks}/{len(countries)}；同值并列排名，缺失或质量排除不排名。"
                   "排名固定 BEV，不随动力焦点改变；英国目前未通过跨来源可比性验证。")
        rank_table = rankings.sort_values([rank_column, "geo"], na_position="last").copy()
        rank_table["share_status"] = rank_table.share_status.map(lambda token: ui.REASONS.get(token, token))
        rank_table["yoy_status"] = rank_table.yoy_status.map(lambda token: ui.REASONS.get(token, token))
        st.dataframe(rank_table[["country", rank_column, "bev_value", "total_value", "delta", "yoy_pct", "bev_share_pct",
                                 "status", "previous_status", "total_status", "yoy_status", "share_status"]],
                     hide_index=True, width="stretch", column_config={"country": "国家", rank_column: "当前指标名次",
                         "bev_value": "BEV 注册量（辆）", "total_value": "全部新车 TOTAL（辆）", "delta": "BEV 净增（辆）",
                         "yoy_pct": st.column_config.NumberColumn("BEV 同比（%）", format="%.2f"),
                         "bev_share_pct": st.column_config.NumberColumn("BEV 全新车占比（%）", format="%.2f"),
                         "status": "BEV 标记", "previous_status": "上年 BEV 标记", "total_status": "TOTAL 标记",
                         "yoy_status": "同比说明", "share_status": "占比说明"})
        st.download_button("下载国家排名 CSV", rankings.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"bev_country_rankings_{year}_{batch_id}.csv", mime="text/csv", disabled=overview_active)
        ui.panel_title("观察覆盖审计", "按三类组合检查真实观察、缺失及质量排除；不代表市场份额")
        coverage = unified.coverage(analytics.prepare_observations(available_raw, exclude_imputed=exclude), list(TARGET_COUNTRIES), [year])
        st.dataframe(coverage, hide_index=True, width="stretch", column_config={"country": "国家", "year": "年份",
                     "expected_count": "预期三类组合", "observed_count": "真实有值组合", "available_count": "当前可用组合",
                     "missing_count": "缺失或未采集", "excluded_count": "质量排除", "imputed_count": "官方插补组合",
                     "coverage_pct": st.column_config.ProgressColumn("组合可用率（%）", min_value=0, max_value=100, format="%.0f%%")})
        st.caption('覆盖计数只包含三类原始动力。英国有数据不代表进入严格比较；0/3 不是零辆。')
    with brief:
        ui.panel_title(f"{year} 年商业汇报摘要", "真实数据、计算结果与分析提示分开呈现 · 可下载一页打印版")
        if overview_active:
            st.info('概览摘要保留每国来源，不提供混合来源合计或排名；多国明细可能需要多页打印。')
            st.dataframe(displayed.loc[displayed.year.eq(year), ['geo', 'powertrain_zh', 'value', 'yoy_pct', 'source_id', 'scope_note']], hide_index=True)
        st.dataframe(rankings[["country", "bev_value", "total_value", "delta", "yoy_pct", "bev_share_pct", "status", "total_status"]],
                     hide_index=True, width="stretch", column_config={"country": "国家", "bev_value": "BEV 注册量（辆）",
                         "total_value": "全部新车 TOTAL（辆）", "delta": "BEV 净增（辆）",
                         "yoy_pct": st.column_config.NumberColumn("BEV 同比（%）", format="%.2f"),
                         "bev_share_pct": st.column_config.NumberColumn("BEV 全新车占比（%）", format="%.2f")})
        for fact in unified.insights(prepared, countries, energies, year)[:5]:
            ui.insight(fact["text"])
        st.caption("国家表固定 BEV；提示来自所选动力，最多五条并按绝对净变动排序。仅使用所选已接入国家，不推断政策原因。")
        summary_html = (unified.overview_html(prepared, countries, energies, year) if overview_active else
                        reporting.build_summary_html(prepared, batch, rank_countries, energies, year, totals=euro_totals))
        st.download_button("下载一页商业汇报 HTML", summary_html,
                           file_name=f"ev_market_brief_{year}_{batch_id}.html", mime="text/html", width="stretch")
        st.caption("用浏览器打开下载的 HTML，可离线展示；Ctrl+P 选择横向打印或保存 PDF。")
        summary = displayed.loc[displayed.year.eq(year)].copy() if overview_active else reporting.summary_rows(prepared, countries, energies, year)
        with st.expander("完整动力明细与 CSV"):
            st.dataframe(summary, hide_index=True, width="stretch", column_config={"同比（%）": st.column_config.NumberColumn(format="%.2f")})
            st.download_button("下载完整动力明细 CSV", summary.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"powertrain_brief_details_{year}_{batch_id}.csv", mime="text/csv")
    with evidence:
        ui.panel_title("来源、口径与质量", "SQLite 中的真实值 · 四层来源校验 · 不依赖 Eurostat 在线访问")
        st.write("数据集：", batch["dataset"], "官方更新时间：", batch["source_updated"])
        st.write("采集批次：", batch_id, "采集完成（UTC）：", batch["retrieved_at_utc"])
        st.write("观察定义：年度新乘用车首次注册，进口二手车不计入；单位 NR 为辆。")
        st.write("官方 i 标记表示 Eurostat 或其他接收机构插补。空标记仅表示该响应未附加标记。")
        st.code(batch["request_url"], language=None)
        st.dataframe(euro_raw, hide_index=True, width="stretch")
        st.caption(f"Eurostat 批次 {len(euro_raw)} 条三类动力原始记录；来源与数据库逐字段校验通过。")
        if uk_metadata:
            st.write('英国来源：DfT / DVLA；英国包含北爱尔兰，首次注册含进口二手车。')
            st.caption('原始 TOTAL 以 Thousands、1 位小数公布；图表换算为辆仍是约数。隐藏缓存只供追溯。')
            st.caption(dft.ATTRIBUTION)
            st.link_button('DfT 官方统计与附件', dft.SOURCE_PAGE)
            st.link_button('Open Government Licence v3.0', dft.LICENSE_URL)
            st.dataframe(uk_original, hide_index=True, width='stretch')
            st.download_button('导出英国官方原始单位 CSV', unified.export_csv(uk_original.assign(
                source_id=dft.SOURCE, license_url=dft.LICENSE_URL, attribution=dft.ATTRIBUTION)),
                file_name=f'dft_original_{uk_id}.csv', mime='text/csv')
            st.json({k: uk_metadata[k] for k in ('batch_id','retrieved_at_utc','source_updated','scope_note','comparability')})
        st.write("来源身份：", batch.get("source_id", "EUROSTAT"), "统计范围：", batch.get("scope_id", "new_passenger_registrations"))
        with st.expander("全部新车 TOTAL 分母与来源"):
            if euro_totals.empty:
                st.info("当前历史快照没有同批次 TOTAL，无法计算全新车占比；三类内部构成和原有指标仍可使用。")
            else:
                st.dataframe(euro_totals, hide_index=True, width="stretch")
                st.caption(f"{len(euro_totals)} 条 Eurostat TOTAL 观察；不能与三类电动车数量再次相加。")
                st.download_button("导出 TOTAL 原始数据 CSV", euro_totals.to_csv(index=False).encode("utf-8-sig"),
                                   file_name=f"total_registrations_{batch_id}.csv", mime="text/csv")
        with st.expander("查看来源文件与摘要"):
            st.json({key: batch[key] for key in ("raw_file", "csv_file", "raw_sha256", "csv_sha256")})
        if len(batches) > 1:
            old_id = st.selectbox("修订对比：选择另一个历史快照", [value for value in batch_ids if value != batch_id])
            _, old_raw = batch_data(old_id, signature)
            st.dataframe(analytics.compare_batches(old_raw, euro_raw), hide_index=True, width="stretch")
        else:
            st.info("目前只有一个真实批次。未来增加已验证批次后，这里将支持值和质量标记的修订对比。")
    with roadmap:
        ui.panel_title("下一步数据扩展", "接口已预留 · 各模块必须独立提供真实来源")
        st.markdown("更多欧洲国家可沿用注册数据接口；车企与政策数据需声明各自统计口径、时间粒度和证据链接。")
        st.markdown("AI 分析接口只预留结构化事实及证据输入约定。本阶段未配置 AI API 或政策原因推断。")
        st.caption("模块约定在 evmi/contracts.py；后续接入前先扩展真实数据采集与验证。")
    st.caption("Eurostat / DfT 官方年度数据 · 真实观察、计算结果与分析提示 · PNG 在浏览器端生成；跨源不合计")
    synchronize_theme()


if __name__ == "__main__":
    main()
