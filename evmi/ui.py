"""统一设计规范；只为自有 evmi CSS 类写样式，不依赖内部 DOM 选择器。"""

from html import escape
import pandas as pd
import streamlit as st

from evmi.markets import COUNTRY_NAMES
REASONS = {
    "ok": "可比样本完整", "no_previous_year": "本批次没有上一年数据",
    "previous_zero": "上一年为零，同比不可计算", "current_incomplete": "当年分项不完整",
    "previous_incomplete": "上一年分项不完整", "no_selection": "未选择分析范围",
    "current_missing": "当年数值缺失", "previous_missing": "上一年数值缺失",
    "current_excluded": "当年包含被排除的插补值", "previous_excluded": "上一年插补值被排除",
    "total_missing": "缺少同批次全部新车分母", "total_excluded": "全部新车分母含被排除的插补值",
    "total_zero": "全部新车分母为零", "incomparable_scope": "分子分母统计范围不一致",
    "numerator_gt_total": "纯电数量超过全部新车分母，需核查口径", "coverage_incomplete": "分子或分母覆盖不完整",
    "rounded_dft_total": "约数：DfT TOTAL 公布千辆、1位小数；含进口二手车",
}


def present(value):
    return value is not None and not pd.isna(value)


def number(value, signed=False):
    if not present(value):
        return "—"
    return f"{int(value):+,}" if signed else f"{int(value):,}"


def percent(value):
    return f"{float(value):+.2f}%" if present(value) else "—"


def theme_name():
    try:
        return st.context.theme.type or "dark"
    except (AttributeError, RuntimeError):
        return "dark"


def inject_style(theme="dark"):
    colors = ("#101E33", "#DFE8F5", "#8BA0BD", "#26374E", "#38BDF8") if theme == "dark" else (
        "#FFFFFF", "#17263D", "#52657D", "#D6DFEB", "#027DAA")
    panel, text, muted, border, accent = colors
    st.html(f"""<style>
    .evmi-header{{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;
      border-bottom:1px solid {border};padding:0 0 20px;margin-bottom:16px;color:{text}}}
    .evmi-eyebrow{{font:600 11px/1.4 'Segoe UI',sans-serif;letter-spacing:2px;color:{accent};text-transform:uppercase}}
    .evmi-title{{font:650 clamp(25px,2.2vw,37px)/1.2 'Segoe UI',sans-serif;margin:8px 0}}
    .evmi-subtitle{{font-size:13px;color:{muted};line-height:1.6}}
    .evmi-badge{{display:inline-block;border:1px solid {border};padding:5px 10px;border-radius:4px;
       color:{muted};font-size:11px;white-space:nowrap;margin:4px 4px 0 0}}
    .evmi-header-status{{text-align:right;min-width:160px}}
    .evmi-kpis{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:8px 0 24px}}
    .evmi-card{{background:{panel};border:1px solid {border};border-radius:8px;padding:17px 16px;min-height:125px}}
    .evmi-card-label{{font-size:12px;color:{muted};margin-bottom:10px}}
    .evmi-card-value{{font:600 clamp(21px,1.7vw,31px)/1.2 'Consolas','Segoe UI',monospace;
       color:{text};font-variant-numeric:tabular-nums;letter-spacing:-.7px}}
    .evmi-card-note{{font-size:11px;color:{muted};line-height:1.5;margin-top:9px}}
    .evmi-positive{{color:{'#5EE0AF' if theme == 'dark' else '#087D58'} !important}}
    .evmi-negative{{color:{'#FDA4AF' if theme == 'dark' else '#C6334E'} !important}}
    .evmi-panel-title{{font-size:17px;font-weight:600;color:{text};margin:2px 0 5px}}
    .evmi-panel-kicker{{color:{muted};font-size:11px;letter-spacing:1px;margin:0 0 8px}}
    .evmi-insight{{border-left:3px solid {accent};background:{panel};padding:13px 16px;
       font-size:13px;line-height:1.65;margin:8px 0;color:{text}}}
    .evmi-insight-label{{font-size:10px;letter-spacing:1px;color:{accent};margin-bottom:5px}}
    .evmi-sidebar-brand{{font-size:21px;font-weight:700;letter-spacing:1px;color:{text};margin:0 0 4px}}
    @media(max-width:1400px){{.evmi-kpis{{grid-template-columns:repeat(3,minmax(0,1fr))}}
      .evmi-card{{min-height:112px}}}}
    @media(max-width:850px){{.evmi-kpis{{grid-template-columns:repeat(2,minmax(0,1fr))}}
      .evmi-header{{flex-direction:column;gap:8px}}.evmi-header-status{{text-align:left}}}}
    </style>""")


def header(batch, year, countries, energies, frame):
    scope = " / ".join(COUNTRY_NAMES.get(code, code) for code in countries)
    st.html(f"""<div class="evmi-header"><div>
      <div class="evmi-eyebrow">EV MARKET INTELLIGENCE OS · RESEARCH TERMINAL</div>
      <div class="evmi-title">欧洲新能源汽车市场研究终端</div>
      <div class="evmi-subtitle">{escape(scope)} · {year} 年分析 · {len(energies)} 类动力 · 年度注册量（各来源口径见说明）</div>
      </div><div class="evmi-header-status"><span class="evmi-badge">LOCAL SQLITE</span>
      <span class="evmi-badge">来源已验证</span><br><span class="evmi-badge">{escape('Eurostat + DfT/DVLA' if 'GB' in countries else 'Eurostat')}</span>
      <div class="evmi-subtitle">观察期 {int(frame.year.min())}—{int(frame.year.max())} · 暂无月度数据</div>
      </div></div>""")


def metric_cards(aggregate, bev_share, quality_count, year, scope_count, market_share=None, country=None):
    def movement(value):
        return "evmi-positive" if present(value) and value > 0 else "evmi-negative" if present(value) and value < 0 else ""
    cards = [
        ("所选车型注册量" + (' · '+COUNTRY_NAMES.get(country,country) if country else ''), number(aggregate["value"]), f"{year} 年 · 辆 · {scope_count} 个国家", ""),
        ("计算结果 · 年度绝对净增", number(aggregate["delta"], True),
         f"与 {year - 1} 年相同国家与动力集合比较", movement(aggregate["delta"])),
        ("计算结果 · 年度同比", percent(aggregate["yoy_pct"]),
         REASONS.get(aggregate["reason"], aggregate["reason"]), movement(aggregate["yoy_pct"])),
        ("计算结果 · BEV 内部构成", f"{float(bev_share):.2f}%" if present(bev_share) else "—",
         "分母为所选国家的三类插电车型完整合计", ""),
        ("数据质量 · 官方插补记录", str(quality_count), "分析年内的 i 标记条数 · 非车辆数量", ""),
    ]
    if market_share is not None:
        cards.insert(3, ("计算结果 · BEV 首次注册占比（约）" if country=='GB' else "计算结果 · BEV 全新车占比",
                         ('≈ ' if country=='GB' else '') + f"{float(market_share['value']):.2f}%" if present(market_share.get("value")) else "—",
                         "BEV ÷ 同批次 TOTAL；" + REASONS.get(market_share.get("reason"), str(market_share.get("reason", ""))), ""))
    content = "".join(f'<div class="evmi-card"><div class="evmi-card-label">{escape(label)}</div>'
                      f'<div class="evmi-card-value {klass}">{escape(value)}</div>'
                      f'<div class="evmi-card-note">{escape(note)}</div></div>' for label, value, note, klass in cards)
    st.html(f'<div class="evmi-kpis">{content}</div>')


def panel_title(title, description):
    st.html(f'<div class="evmi-panel-title">{escape(title)}</div>'
            f'<div class="evmi-panel-kicker">{escape(description)}</div>')


def insight(text):
    st.html(f'<div class="evmi-insight"><div class="evmi-insight-label">分析提示 · 基于观察与计算</div>'
            f'{escape(text)}</div>')
