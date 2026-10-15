"""Streamlit Community Cloud入口；只展示本地已验证快照，不启动采集或模型调用。"""
from pathlib import Path
import runpy
from evmi import public_mode

public_mode.activate()
runpy.run_path(str(Path(__file__).resolve().with_name('app.py')), run_name='__main__')
