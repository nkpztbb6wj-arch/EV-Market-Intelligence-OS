"""作品首页；覆盖范围和时间从已验证快照读取，不把构建时间当成数据更新时间。"""
import streamlit as st
from evmi import data, dft
from evmi.markets import COUNTRY_NAMES


@st.cache_data(ttl=60, show_spinner=False)
def snapshot_info():
    batches, warnings = data.list_verified_batches()
    if not batches or warnings:
        raise ValueError('发布快照未通过完整来源校验')
    eu, raw = data.load_batch(batches[0]['batch_id'])
    sources = [('Eurostat', eu['source_updated'], eu['retrieved_at_utc'])]
    countries = set(raw.geo)
    years = set(int(y) for y in raw.year)
    uk_batches = dft.list_batches()
    if uk_batches:
        uk, rows = dft.load_batch(uk_batches[0]['batch_id'])
        countries.update(rows.geo)
        years.update(int(y) for y in rows.year)
        sources.append(('DfT / DVLA', ', '.join(sorted(set(rows.source_updated))), uk['retrieved_at_utc']))
    return sorted(countries), sorted(years), sources


def landing():
    """返回True表示停在首页；False继续执行同一个Dashboard。"""
    if st.session_state.get('public_started'):
        if st.button('← 作品介绍', key='public_home'):
            st.session_state['public_started'] = False
            st.rerun()
        st.caption('公开只读演示 · 已验证快照 · 不实时采集 · 不调用付费AI')
        return False
    st.title('EV Market Intelligence OS')
    st.subheader('欧洲新能源汽车市场研究 · 交互式个人作品')
    st.write('将官方车辆注册数据转化为可筛选、可追溯的市场比较，展示Python数据处理、SQL数据治理与商业分析能力。')
    try:
        countries, years, sources = snapshot_info()
    except (ValueError, OSError):
        st.error('发布快照不完整，请联系作品维护者。')
        return True
    cols = st.columns(3)
    cols[0].metric('重点市场', len(countries))
    cols[1].metric('年度覆盖', f'{min(years)}—{max(years)}')
    cols[2].metric('官方来源', len(sources))
    st.write('覆盖：' + '、'.join(COUNTRY_NAMES[c] for c in countries))
    st.markdown('**可体验内容**：年度注册趋势、同比与净增、BEV渗透率、动力结构、国家排名及地图；支持国家/年份/动力/质量筛选、历史批次、深浅主题，以及CSV、PNG、HTML导出。')
    st.warning('英国DfT首次注册含进口二手车，TOTAL为官方公布精度换算约数。11国可以并列展示；严格可比模式排除英国，不跨来源合计或严格排名。所选国家不是整个欧洲或欧盟市场。')
    a, b = st.columns(2)
    if a.button('进入 Dashboard', key='public_dashboard', type='primary', width='stretch'):
        st.session_state['public_started'] = True
        st.rerun()
    if b.button('查看 AI Market Research 演示', key='public_ai', width='stretch'):
        st.session_state['public_started'] = True
        st.session_state['ai_workspace'] = True
        st.rerun()
    st.info('AI报告演示：Python准备真实数字与来源，可下载结构化输入并核验外部报告JSON。公开版不调用模型、不保存访客文本到服务器；没有AI生成的结果不会标为AI生成。')
    st.markdown('**数据状态：固定快照，暂未启用自动更新。**')
    for name, updated, collected in sources:
        st.caption(f'{name} · 官方更新：{updated} · 采集时间UTC：{collected}')
    st.markdown('[Eurostat road_eqr_carpda](https://ec.europa.eu/eurostat/databrowser/product/page/road_eqr_carpda) · [英国DfT / DVLA](https://www.gov.uk/government/statistical-data-sets/vehicle-licensing-statistics-data-tables)')
    st.caption(dft.ATTRIBUTION)
    return True
