"""src/invest_research/financial/statement_cn_labels.py
三张报表常见 GAAP 行名 → 中文（方案 B 原表行名翻译；未命中保留原文，
不阻塞、不猜）。覆盖 NVDA/AMZN/AAPL/MSFT/GOOGL/JPM 实测行名及常用变体，
含银行业（净利息收入、信贷损失拨备、交易性资产等）特有行名。
"""

from __future__ import annotations

import re

# 行名末尾的括号备注（SEC 表格为数值补充说明，如 "Loans (included $70,684 and
# $41,350 at fair value)"），翻译前剥离以匹配基础行名。
_PAREN_NOTE_RE = re.compile(r"\s*\(.*?\)\s*$")

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
    # 银行业（JPM 实测）——利润表
    "Investment banking fees": "投资银行手续费",
    "Principal transactions": "本金交易收益",
    "Lending- and deposit-related fees": "贷款及存款相关手续费",
    "Asset management fees": "资产管理手续费",
    "Commissions and other fees": "佣金及其他手续费",
    "Investment securities losses": "投资证券损失",
    "Mortgage fees and related income": "抵押贷款手续费及相关收入",
    "Card income": "银行卡收入",
    "Other income": "其他收入",
    "Noninterest revenue": "非利息收入",
    "Net interest income": "净利息收入",
    "Total net revenue": "净收入合计",
    "Provision for credit losses": "信贷损失拨备",
    "Noninterest expense": "非利息支出",
    "Compensation expense": "薪酬支出",
    "Occupancy expense": "占用成本",
    "Technology, communications and equipment expense": "技术、通讯及设备支出",
    "Professional and outside services": "专业及外部服务费",
    "Marketing": "营销费用",
    "Other expense": "其他支出",
    "Total noninterest expense": "非利息支出合计",
    "Income before income tax expense": "税前利润",
    "Income tax expense": "所得税费用",
    "Net income applicable to common stockholders": "归属于普通股股东的净利润",
    "Net income per common share data": "每股普通股收益数据",
    "Basic earnings per share": "基本每股收益",
    "Diluted earnings per share": "稀释每股收益",
    "Weighted-average basic shares": "加权平均基本股数",
    "Weighted-average diluted shares": "加权平均稀释股数",
    # 银行业——资产负债表
    "Cash and due from banks": "现金及存放同业款项",
    "Deposits with banks": "存放同业款项",
    "Federal funds sold and securities purchased under resale agreements": (
        "联邦基金出售及回购协议下买入证券"
    ),
    "Securities borrowed": "借入证券",
    "Trading assets": "交易性资产",
    "Available-for-sale securities": "可供出售证券",
    "Held-to-maturity securities": "持有至到期证券",
    "Investment securities, net of allowance for credit losses": "投资证券（扣除信贷损失准备）",
    "Loans": "贷款",
    "Allowance for loan losses": "贷款损失准备",
    "Loans, net of allowance for loan losses": "贷款净额（扣除贷款损失准备）",
    "Accrued interest and accounts receivable": "应计利息及应收账款",
    "Premises and equipment": "房产及设备",
    "Goodwill, MSRs and other intangible assets": "商誉、抵押服务权及其他无形资产",
    "Deposits": "存款",
    "Federal funds purchased and securities loaned or sold under repurchase agreements": (
        "联邦基金购入及证券贷出或回购协议下卖出"
    ),
    "Short-term borrowings": "短期借款",
    "Trading liabilities": "交易性负债",
    "Accounts payable and other liabilities": "应付账款及其他负债",
    "Beneficial interests issued by consolidated VIEs": "合并可变利益实体发行的受益权益",
    "Accumulated other comprehensive losses": "累计其他综合亏损",
    "Treasury stock, at cost": "库存股（按成本）",
    # 银行业——现金流量表
    "Operating activities": "经营活动",
    "Investing activities": "投资活动",
    "Financing activities": "筹资活动",
    "Deferred tax (benefit)/expense": "递延所得税（收益）/费用",
    "Estimated bargain purchase gain associated with the First Republic acquisition": (
        "First Republic 收购相关估计廉价收购利得"
    ),
    "Initial gain on the Visa share exchange": "Visa 股份交换初始利得",
    "Originations and purchases of loans held-for-sale": "持有待售贷款发放及购买",
    "Proceeds from sales, securitizations and paydowns of loans held-for-sale": (
        "持有待售贷款出售、证券化及还款所得"
    ),
    "Net change in:": "净变动：",
    "Other operating adjustments": "其他经营调整",
    "Net cash (used in)/provided by operating activities": "经营活动现金流量净额",
    "Held-to-maturity securities:": "持有至到期证券：",
    "Proceeds from paydowns and maturities": "还款及到期所得",
    "Purchases": "购买",
    "Available-for-sale securities:": "可供出售证券：",
    "Proceeds from sales": "出售所得",
    "Proceeds from sales and securitizations of loans held-for-investment": (
        "持有至投资贷款出售及证券化所得"
    ),
    "Other changes in loans, net": "其他贷款变动净额",
    "Net cash used in First Republic Acquisition": "First Republic 收购使用现金净额",
    "All other investing activities, net": "其他投资活动净额",
    "Net cash (used in)/provided by investing activities": "投资活动现金流量净额",
    "Proceeds from long-term borrowings": "长期借款所得",
    "Payments of long-term borrowings": "长期借款偿还",
    "Proceeds from issuance of preferred stock": "发行优先股所得",
    "Redemption of preferred stock": "优先股赎回",
    "Treasury stock repurchased": "库存股回购",
    "All other financing activities, net": "其他筹资活动净额",
    "Net cash provided by/(used in) financing activities": "筹资活动现金流量净额",
    "Effect of exchange rate changes on cash and due from banks and deposits with banks": (
        "汇率变动对现金及存放同业款项的影响"
    ),
    "Net increase/(decrease) in cash and due from banks and deposits with banks": (
        "现金及存放同业款项净增加（减少）"
    ),
    "Cash and due from banks and deposits with banks at the beginning of the period": (
        "期初现金及存放同业款项"
    ),
    "Cash and due from banks and deposits with banks at the end of the period": (
        "期末现金及存放同业款项"
    ),
    "Cash interest paid": "支付利息现金",
    "Cash income taxes paid, net": "支付所得税现金净额",
}

# 后缀翻译：未命中的完整行名按末词回退（如 "Total X" → "X合计"）。
_SUFFIX_CN = {"Total": "合计", "Net": "净额", "Gross": "毛额"}


def translate_statement_row(label_en: str) -> str:
    """翻译报表行名；未命中先剥离尾部括号备注、再试前缀回退，仍无则返回原文。"""
    key = label_en.strip()
    if key in _LABELS:
        return _LABELS[key]
    # 剥离尾部括号备注（SEC 表格的数值说明，如 "Loans (included $70,684 ... at fair value)"）。
    stripped = _PAREN_NOTE_RE.sub("", key).strip()
    if stripped and stripped != key and stripped in _LABELS:
        return _LABELS[stripped]
    words = key.split()
    if len(words) >= 2 and words[0] in _SUFFIX_CN:
        rest = " ".join(words[1:])
        if rest in _LABELS:
            return f"{_LABELS[rest]}{_SUFFIX_CN[words[0]]}"
    return key
