"""src/invest_research/financial/statement_cn_labels.py
三张报表常见 GAAP 行名 → 中文（方案 B 原表行名翻译；未命中保留原文，
不阻塞、不猜）。覆盖 NVDA/AMZN/AAPL/MSFT 实测行名及常用变体。
"""

from __future__ import annotations

_LABELS: dict[str, str] = {
    # 利润表
    "Revenue": "营业收入",
    "Cost of revenue": "销售成本",
    "Gross profit": "毛利润",
    "Operating expenses": "营业费用",
    "Research and development": "研发费用",
    "Sales, general and administrative": "销售、一般及管理费用",
    "Acquisition termination cost": "收购终止成本",
    "Total operating expenses": "营业费用合计",
    "Operating income": "营业利润",
    "Operating income (loss)": "营业利润（亏损）",
    "Interest income": "利息收入",
    "Interest expense": "利息支出",
    "Other, net": "其他净额",
    "Other income (expense), net": "其他收益（费用）净额",
    "Income before income tax": "税前利润",
    "Income tax expense (benefit)": "所得税费用（收益）",
    "Net income": "净利润",
    "Net income (loss)": "净利润（亏损）",
    "Net income per share:": "每股收益：",
    "Basic": "基本",
    "Diluted": "稀释",
    "Weighted average shares used in per share computation": "每股收益计算使用的加权平均股数",
    # 资产负债表
    "Assets": "资产",
    "Current assets:": "流动资产：",
    "Cash and cash equivalents": "现金及现金等价物",
    "Marketable securities": "有价证券",
    "Accounts receivable, net": "应收账款净额",
    "Inventories": "存货",
    "Prepaid expenses and other current assets": "预付款项及其他流动资产",
    "Total current assets": "流动资产合计",
    "Property and equipment, net": "物业及设备净额",
    "Operating lease assets": "经营租赁资产",
    "Goodwill": "商誉",
    "Intangible assets, net": "无形资产净额",
    "Deferred income tax assets": "递延所得税资产",
    "Other assets": "其他资产",
    "Total assets": "资产总计",
    "Liabilities and Shareholders' Equity": "负债及股东权益",
    "Current liabilities:": "流动负债：",
    "Accounts payable": "应付账款",
    "Accrued and other current liabilities": "应计及其他流动负债",
    "Short-term debt": "短期债务",
    "Total current liabilities": "流动负债合计",
    "Long-term debt": "长期债务",
    "Long-term operating lease liabilities": "长期经营租赁负债",
    "Other long-term liabilities": "其他长期负债",
    "Total liabilities": "负债合计",
    "Commitments and contingencies": "承诺及或有事项",
    "Shareholders' equity:": "股东权益：",
    "Preferred stock": "优先股",
    "Common stock": "普通股",
    "Additional paid-in capital": "额外实收资本",
    "Accumulated other comprehensive income": "累计其他综合收益",
    "Retained earnings": "留存收益",
    "Total shareholders' equity": "股东权益合计",
    "Total liabilities and shareholders' equity": "负债及股东权益合计",
    # 现金流量表
    "Cash flows from operating activities:": "经营活动现金流量：",
    "Adjustments to reconcile net income to net cash provided by operating activities": (
        "将净利润调整为经营活动现金流量的调整项"
    ),
    "Stock-based compensation expense": "股权激励费用",
    "Depreciation and amortization": "折旧与摊销",
    "Deferred income taxes": "递延所得税",
    "(Gains) losses on non-marketable equity securities and other investments": (
        "非有价权益证券及其他投资损益"
    ),
    "Changes in operating assets and liabilities, net of acquisitions": (
        "经营性资产负债变动（净额）"
    ),
    "Net cash provided by operating activities": "经营活动产生的现金流量净额",
    "Cash flows from investing activities:": "投资活动现金流量：",
    "Proceeds from maturities of marketable securities": "有价证券到期收回",
    "Proceeds from sales of marketable securities": "出售有价证券所得",
    "Purchases of marketable securities": "购买有价证券",
    "Purchases related to property and equipment and intangible assets": "物业设备及无形资产购置",
    "Acquisitions, net of cash acquired": "收购（扣除取得现金净额）",
    "Net cash provided by (used in) investing activities": "投资活动产生的现金流量净额",
    "Cash flows from financing activities:": "筹资活动现金流量：",
    "Proceeds related to employee stock plans": "员工购股计划所得",
    "Payments related to repurchases of common stock": "回购普通股支出",
    "Payments related to tax on restricted stock units": "限制性股票代扣税支出",
    "Repayment of debt": "偿还债务",
    "Dividends paid": "支付股息",
    "Net cash used in financing activities": "筹资活动产生的现金流量净额",
    "Change in cash and cash equivalents": "现金及现金等价物净变动",
    "Cash and cash equivalents at beginning of period": "期初现金及现金等价物",
    "Cash and cash equivalents at end of period": "期末现金及现金等价物",
    "Supplemental disclosures of cash flow information:": "现金流量表补充披露：",
    "Cash paid for income taxes, net": "支付所得税现金净额",
    "Cash paid for interest": "支付利息现金",
}

# 后缀翻译：未命中的完整行名按末词回退（如 "Total X" → "X合计"）。
_SUFFIX_CN = {"Total": "合计", "Net": "净额", "Gross": "毛额"}


def translate_statement_row(label_en: str) -> str:
    """翻译报表行名；未命中先试 '末词+原文' 回退，仍无则返回原文。"""
    key = label_en.strip()
    if key in _LABELS:
        return _LABELS[key]
    words = key.split()
    if len(words) >= 2 and words[0] in _SUFFIX_CN:
        rest = " ".join(words[1:])
        if rest in _LABELS:
            return f"{_LABELS[rest]}{_SUFFIX_CN[words[0]]}"
    return key
