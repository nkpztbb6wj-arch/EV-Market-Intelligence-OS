"""AI报告工作区；显式点击才构建输入或调用模型，筛选变化自动使旧结果失效。"""
import json
import streamlit as st
from evmi import ai_reports as engine, data, dft
from evmi.markets import COUNTRY_NAMES
from evmi import public_mode

def render():
    st.subheader('AI Market Research')
    st.caption('真实SQLite事实 → Python计算 → 模型组织文字 → 数字与引用检查。商业叙述仍需人工复核。')
    batches,warnings=data.list_verified_batches()
    if not batches:
        st.warning('没有已验证Eurostat批次'); return
    for warning in warnings: st.warning(warning)
    euro=st.selectbox('报告Eurostat批次',[b['batch_id'] for b in batches],key='ai_euro')
    uk_options=[None,*[b['batch_id'] for b in dft.list_batches()]]
    uk=st.selectbox('报告英国独立批次',uk_options,index=1 if len(uk_options)>1 else 0,key='ai_uk',format_func=lambda x:x or '不使用英国来源')
    scope=st.selectbox('报告范围',['十国Eurostat比较','英国DfT分析','自定义'],key='ai_scope')
    _,raw=data.load_batch(euro)
    options=list(sorted(raw.geo.unique()))+(['GB'] if uk else [])
    default=['GB'] if scope=='英国DfT分析' and uk else sorted(raw.geo.unique())
    countries=st.multiselect('报告国家',options,default=default,key='ai_countries_'+scope,format_func=lambda c:COUNTRY_NAMES.get(c,c))
    years=st.multiselect('报告年份',sorted(int(y) for y in raw.year.unique()),default=[2025],key='ai_years')
    kind=st.selectbox('报告类型',['管理层简报','详细市场研究报告'],key='ai_kind')
    language=st.selectbox('报告语言',['中文','English'],key='ai_language')
    exclude=st.checkbox('报告排除官方插补',key='ai_exclude')
    identity=engine.fingerprint([scope,countries,years,kind,language,euro,uk,exclude])
    if st.session_state.get('ai_selection')!=identity:
        # 事实编号只属于当前输入；旧粘贴文本不能套用到另一批筛选结果。
        for key in ('ai_payload','ai_report','ai_last_attempt','ai_import_text'): st.session_state.pop(key,None)
        st.session_state['ai_selection']=identity
    if st.button('准备真实数据与ChatGPT输入',key='ai_prepare'):
        try:
            if scope=='十国Eurostat比较' and 'GB' in countries: raise ValueError('Eurostat报告范围不包含英国，请改选自定义')
            if scope=='英国DfT分析' and countries!=['GB']: raise ValueError('英国报告请选择英国；多国使用自定义')
            st.session_state['ai_payload']=engine.build_input(countries,years,kind,language,euro,uk,exclude)
            st.session_state.pop('ai_report',None)
            st.session_state.pop('ai_last_attempt',None)
        except (ValueError,OSError) as exc: st.error(str(exc))
    payload=st.session_state.get('ai_payload')
    if payload is None:
        st.info('准备数据无需密钥，不会调用模型或产生API费用。');return
    st.success(f"已准备 {len(payload['facts'])} 项真实事实与计算；这不是AI报告。")
    st.warning(engine.CN_LIMITATIONS)
    st.download_button('下载结构化分析输入 JSON',engine.dumps(payload),file_name='analysis_input.json',mime='application/json',key='ai_input_download')
    st.download_button('下载可复制给ChatGPT的提示词',engine.prompt(payload),file_name='chatgpt_input.txt',key='ai_prompt_download')
    with st.expander('查看结构化输入'): st.json(payload)
    if public_mode.enabled():
        st.info('公开只读演示：模型调用永久关闭，报告仅在当前会话预览和下载，不在服务器保存。请勿粘贴个人敏感信息。')
    else:
        config=engine.configuration()
        st.caption('接口：OpenAI Responses API；模型：'+(config['model'] or '未配置')+'；密钥：'+('已配置' if config['key_present'] else '未配置'))
        price_in=st.number_input('已核实输入单价（美元/百万token）',min_value=0.0,value=0.0,key='ai_price_in')
        price_out=st.number_input('已核实输出单价（美元/百万token）',min_value=0.0,value=0.0,key='ai_price_out')
        cap=st.number_input('单次预算（美元）',min_value=0.01,max_value=5.0,value=0.10,key='ai_budget')
        tokens=st.slider('最大输出token',512,6000,2500,key='ai_tokens')
        if price_in>0 and price_out>0:
            st.caption(f'保守费用估算：${engine.cost_bound(engine.prompt(payload),tokens,price_in,price_out):.4f}；估算依赖所填单价，不等于账单保证。')
        approved=st.checkbox('我已确认该模型单价与本次预算，并授权发送这些公开统计数据',key='ai_confirm')
        ready=config['authorized'] and config['key_present'] and bool(config['model']) and approved and price_in>0 and price_out>0
        if not ready: st.info('真实调用待本机配置与付费授权；不要把密钥粘贴到聊天或报告中。')
        if st.button('生成AI报告（真实API）',key='ai_generate',disabled=not ready):
            # 同一输入一次尝试，失败也不自动重试；用户显式重新准备才可再次调用。
            request_id=engine.fingerprint([payload,config['model'],tokens])
            if st.session_state.get('ai_last_attempt')==request_id:
                st.warning('本输入已经尝试过调用。再次调用需重新准备，避免重复扣费。')
            else:
                st.session_state['ai_last_attempt']=request_id
                try:
                    with st.spinner('正在请求模型并核验引用…'):
                        result=engine.call_model(payload,confirmed=approved,max_output=tokens,input_rate=price_in,output_rate=price_out,budget=cap)
                        location=engine.save_report(result,payload)
                        st.session_state['ai_report']=result
                        st.caption('已保存：'+str(location))
                except (ValueError,OSError) as exc: st.error(str(exc))
    text=st.text_area('粘贴ChatGPT返回的报告JSON（可选）',key='ai_import_text',max_chars=50000)
    if st.button('核验并预览外部模型结果',key='ai_import'):
        st.session_state.pop('ai_report',None)
        try:
            result=engine.render(json.loads(text),payload,'用户提供，模型身份未验证','用户粘贴；生成来源未独立验证')
            if not public_mode.enabled():
                engine.save_report(result,payload)
            st.session_state['ai_report']=result
        except (ValueError,TypeError,OSError) as exc: st.error(str(exc))
    report=st.session_state.get('ai_report')
    if report:
        st.warning('数字与引用已检查，商业叙述仍待人工复核。')
        st.markdown(report['markdown'])
        st.download_button('下载报告 Markdown',report['markdown'],file_name='market_report.md',key='ai_md')
        st.download_button('下载报告 HTML',report['html'],file_name='market_report.html',mime='text/html',key='ai_html')
