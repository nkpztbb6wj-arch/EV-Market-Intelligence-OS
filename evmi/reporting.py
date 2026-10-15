"""商业汇报摘要：完全由当前 SQLite 分析结果生成，离线 HTML 可打印。"""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
import pandas as pd

from evmi import analytics
from evmi.ui import number, percent, COUNTRY_NAMES, REASONS


def summary_rows(prepared, countries, energies, year):
    selected = analytics.filter_observations(prepared, countries, energies, [year])
    rows = []
    for row in selected.sort_values(["geo", "mot_nrg"]).to_dict("records"):
        rows.append({"国家": COUNTRY_NAMES.get(row["geo"], row["country"]),
                     "动力": analytics.ENERGY_NAMES.get(row["mot_nrg"], row["mot_nrg"]),
                     "注册量（辆）": row["value"], "绝对净增（辆）": row["delta"],
                     "同比（%）": row["yoy_pct"], "官方标记": row["status"] or "无附加标记",
                     "比较说明": row["yoy_status"], "反转": bool(row["reversal"]),
                     "最大变动": bool(row["is_max_change"])})
    return pd.DataFrame(rows)


def build_summary_html(prepared, batch, countries, energies, year, totals=None):
    """HTML 无外部字体、脚本、在线数据请求；浏览器打印可保存一页 PDF。"""
    aggregate = analytics.aggregate_year(prepared, countries, energies, year)
    facts = selected_facts(prepared, countries, energies, year, limit=5)
    # 十国扩展使用国家级核心表，完整动力明细仍由 summary_rows() 和 CSV 提供。
    if totals is not None:
        return _country_summary_html(prepared, totals, batch, countries, energies, year, aggregate, facts)
    table = summary_rows(prepared, countries, energies, year)
    rows = ""
    for row in table.to_dict("records"):
        cells = [row["国家"], row["动力"], number(row["注册量（辆）"]), number(row["绝对净增（辆）"], True),
                 percent(row["同比（%）"]), row["官方标记"]]
        rows += "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in cells) + "</tr>"
    observations = "".join(f"<li>{escape(fact['text'])}</li>" for fact in facts)
    if not observations:
        observations = "<li>当前选择没有可计算的年度变化，请检查上一年或质量筛选。</li>"
    scope = "、".join(COUNTRY_NAMES.get(code, code) for code in countries)
    coverage = "/".join(analytics.ENERGY_NAMES.get(code, code) for code in energies)
    generated = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    imputation_policy = "排除官方插补值" if prepared.attrs.get("exclude_imputed", False) else "保留官方插补值并标记"
    # 市场原因没有证据，不在摘要里推断补贴、竞争或消费者偏好。
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>EV Market Intelligence · {year}</title>
    <style>@page{{size:A4 landscape;margin:10mm}}*{{box-sizing:border-box}}body{{margin:0;background:#edf1f7;
    font:13px/1.5 'Segoe UI','Microsoft YaHei',sans-serif;color:#17263d}}main{{max-width:1180px;margin:22px auto;
    padding:30px;background:white;border:1px solid #d6dfeb}}header{{border-bottom:2px solid #17263d;padding-bottom:15px}}
    .brand{{font-size:11px;letter-spacing:2px;color:#027daa}}h1{{font-size:27px;margin:8px 0}}h2{{font-size:15px;margin:18px 0 8px}}
    .muted{{font-size:11px;color:#52657d}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:17px 0}}
    .card{{border:1px solid #d6dfeb;padding:13px}}.value{{font:600 25px 'Consolas',monospace;margin:7px 0}}
    table{{width:100%;border-collapse:collapse;font-size:12px}}td,th{{border-bottom:1px solid #d6dfeb;padding:7px;text-align:right}}
    td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){{text-align:left}}th{{background:#f2f5fa}}
    ul{{padding-left:20px;font-size:12px;margin:5px 0}}li{{margin:4px 0}}footer{{margin-top:18px;border-top:1px solid #d6dfeb;
    padding-top:10px;font-size:10px;color:#52657d;overflow-wrap:anywhere}}@media print{{body{{background:white}}
    main{{margin:0;padding:8px;border:0;max-width:none}}}}</style><main>
    <header><div class="brand">EV MARKET INTELLIGENCE OS · EXECUTIVE BRIEF</div><h1>{year} 年插电式乘用车市场分析摘要</h1>
    <div>{escape(scope)} · {escape(coverage)} · 年度新乘用车首次注册 · 单位：辆</div></header>
    <div class="kpis"><div class="card"><div>真实数据 · 所选注册量</div><div class="value">{number(aggregate['value'])}</div></div>
    <div class="card"><div>计算结果 · 绝对净增</div><div class="value">{number(aggregate['delta'],True)}</div></div>
    <div class="card"><div>计算结果 · 同比</div><div class="value">{percent(aggregate['yoy_pct'])}</div></div>
    <div class="card"><div>数据质量 · 插补条数</div><div class="value">{aggregate['imputed_count']}</div></div></div>
    <h2>真实数量与计算结果</h2><table><thead><tr><th>国家</th><th>动力</th><th>注册量（辆）</th><th>净增（辆）</th>
    <th>同比</th><th>官方标记</th></tr></thead><tbody>{rows}</tbody></table>
    <h2>分析提示：仅描述数据变化</h2><ul>{observations}</ul>
    <footer>范围：所选国家及动力；不是全欧洲总量或全新车渗透率。同比比较同一组分项的上一日历年。
    缺失、质量排除或零分母使用 —，不得读作零。质量政策：{imputation_policy}。<br>
    Eurostat · {escape(batch['dataset'])} · 批次 {escape(batch['batch_id'])} · 官方更新时间 {escape(batch['source_updated'])}<br>
    来源：{escape(batch['source_page'])}<br>采集：{escape(batch['retrieved_at_utc'])} · 摘要生成：{generated}（北京时间）。</footer></main></html>"""


def selected_facts(prepared, countries, energies, year, limit=5):
    """摘要优先列出绝对净变动最大的可比观察，仅筛选真实计算结果。"""
    facts = analytics.build_insights(prepared, countries, energies, year)
    selected = analytics.filter_observations(prepared, countries, energies, [year])
    changes = {(row["geo"], row["mot_nrg"]): abs(int(row["delta"]))
               for row in selected.to_dict("records") if pd.notna(row["delta"])}
    return sorted(facts, key=lambda fact: (-changes.get((fact["geo"], fact["mot_nrg"]), 0), fact["geo"], fact["mot_nrg"]))[:limit]


def _country_summary_html(prepared, totals, batch, countries, energies, year, aggregate, facts):
    """A4 横向一页：至多十个已接入国家、至多五条提示，不把未知英国补成零。"""
    exclude = bool(prepared.attrs.get("exclude_imputed", False))
    table = analytics.country_rankings(prepared, totals, countries, year, exclude_imputed=exclude)
    share = analytics.market_share(prepared, totals, countries, year, exclude_imputed=exclude)
    rows = ""
    for row in table.to_dict("records"):
        flags = [f"{label}:{row[field]}" for label, field in (("BEV", "status"), ("上年", "previous_status"), ("TOTAL", "total_status"))
                 if row.get(field) is not None and pd.notna(row.get(field)) and str(row.get(field))]
        cells = [COUNTRY_NAMES.get(row["geo"], row.get("country", row["geo"])), number(row["bev_value"]),
                 number(row["total_value"]), number(row["delta"], True), percent(row["yoy_pct"]),
                 f"{float(row['bev_share_pct']):.2f}%" if pd.notna(row["bev_share_pct"]) else "—", " / ".join(flags) or "无附加标记"]
        rows += "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in cells) + "</tr>"
    brief_facts = selected_facts(prepared, countries, energies, year, limit=5)
    observations = "".join(f"<li>{escape(fact['text'])}</li>" for fact in brief_facts)
    if not observations:
        observations = "<li>当前选择没有可计算的年度变化；没有上一年或有效基数时不推断增长。</li>"
    scope = "、".join(COUNTRY_NAMES.get(code, code) for code in countries)
    energy_scope = "/".join(analytics.ENERGY_NAMES.get(code, code) for code in energies)
    generated = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    policy = "排除官方插补" if exclude else "保留官方插补并标记"
    share_reason = REASONS.get(share["reason"], share["reason"])
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>EV Market Intelligence · {year}</title>
    <style>@page{{size:A4 landscape;margin:9mm}}*{{box-sizing:border-box}}body{{margin:0;background:#edf1f7;
    color:#17263d;font:11px/1.35 'Segoe UI','Microsoft YaHei',sans-serif}}main{{max-width:1120px;margin:16px auto;
    padding:20px;background:#fff;border:1px solid #d6dfeb}}.brand{{font-size:10px;letter-spacing:2px;color:#027daa}}
    h1{{font-size:23px;margin:6px 0}}h2{{font-size:13px;margin:11px 0 5px}}header{{border-bottom:2px solid #17263d;padding-bottom:8px}}
    .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:10px 0}}.card{{border:1px solid #d6dfeb;padding:8px}}
    .value{{font:600 21px 'Consolas',monospace;margin:4px 0}}.muted{{font-size:9px;color:#52657d}}
    table{{width:100%;border-collapse:collapse;font-size:10px}}th,td{{padding:3px 5px;border-bottom:1px solid #d6dfeb;text-align:right}}
    td:first-child,th:first-child,td:last-child,th:last-child{{text-align:left}}th{{background:#f2f5fa}}
    ul{{padding-left:17px;margin:4px 0;font-size:10px}}li{{margin:2px 0}}footer{{font-size:8px;color:#52657d;
    margin-top:8px;padding-top:6px;border-top:1px solid #d6dfeb;overflow-wrap:anywhere}}
    @media print{{body{{background:white}}main{{margin:0;padding:3px;border:0;max-width:none}}table{{break-inside:avoid}}}}
    </style><main><header><div class="brand">EV MARKET INTELLIGENCE OS · EXECUTIVE BRIEF</div>
    <h1>{year} 年新能源汽车市场核心摘要</h1><div>{escape(scope)} · 年度新乘用车首次注册 · 单位：辆</div></header>
    <div class="kpis"><div class="card">真实数据 · 所选动力合计<div class="value">{number(aggregate['value'])}</div><div class="muted">{escape(energy_scope)}</div></div>
    <div class="card">计算结果 · 所选动力同比<div class="value">{percent(aggregate['yoy_pct'])}</div><div class="muted">{escape(REASONS.get(aggregate['reason'], aggregate['reason']))}</div></div>
    <div class="card">计算结果 · BEV 全新车占比<div class="value">{f"{float(share['value']):.2f}%" if pd.notna(share['value']) else "—"}</div><div class="muted">BEV ÷ 同批次 TOTAL · {escape(share_reason)}</div></div>
    <div class="card">数据质量 · 插补观察<div class="value">{aggregate['imputed_count']}</div><div class="muted">所选动力当年记录 · {policy}</div></div></div>
    <h2>已接入国家核心指标 · 固定 BEV 口径</h2><table><thead><tr><th>国家</th><th>BEV 注册量</th><th>全部新车 TOTAL</th>
    <th>BEV 净增</th><th>BEV 同比</th><th>BEV 全新车占比</th><th>官方标记</th></tr></thead><tbody>{rows}</tbody></table>
    <h2>分析提示 · 最多五条，按绝对净变动排序</h2><ul>{observations}</ul>
    <footer>范围：所选已接入国家；不是全欧洲总量。国家表为 BEV，所选动力合计口径见指标卡。英国暂无可比数据，不进入汇总或排名。
    质量政策：{policy}。缺失、排除、零分母使用 —，不是零；完整动力明细另见 CSV。<br>
    Eurostat · {escape(batch['dataset'])} · 批次 {escape(batch['batch_id'])} · 官方更新 {escape(batch['source_updated'])} ·
    来源：{escape(batch['source_page'])}<br>采集 {escape(batch['retrieved_at_utc'])} · 生成 {generated}（北京时间）。</footer></main></html>"""
