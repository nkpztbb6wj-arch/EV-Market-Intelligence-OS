"""未来模块的接入约定；本阶段只定义接口，不请求任何 AI 或付费服务。"""

from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class EvidenceReference:
    """每项事实必须能追溯到来源和批次，方便未来研究报告引用。"""
    dataset: str
    batch_id: str
    source_url: str
    source_updated: str
    retrieved_at_utc: str


class RegistrationProvider(Protocol):
    """更多国家仍需遵守现有国家×年份×动力类型的统计维度。"""
    def load_batch(self, batch_id: str) -> tuple[dict[str, Any], pd.DataFrame]: ...


class VehicleBrandProvider(Protocol):
    """车企数据独立接入，必须声明注册量/销量等口径及时间粒度。"""
    def load_brand_observations(self, countries: list[str], years: list[int]) -> pd.DataFrame: ...


class PolicyProvider(Protocol):
    """政策事实保存生效日期、国家、官方链接；不根据注册变化推断政策。"""
    def load_policy_evidence(self, countries: list[str], years: list[int]) -> pd.DataFrame: ...


class AgentAnalysisProvider(Protocol):
    """仅预留输入约定：AI 将来接收事实及证据，而不是自行编造数据。"""
    def analyze(self, facts: dict[str, Any], evidence: list[EvidenceReference]) -> dict[str, Any]: ...
