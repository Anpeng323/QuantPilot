from . import financial, risk, valuation, sentiment, industry, macro, comparative, dcf, profit, comprehensive
from ai.structured import attach_disclaimer

DISPATCH = {
    "financial": financial.run,
    "financial_summary": financial.run,
    "risk": risk.run,
    "risk_scan": risk.run,
    "valuation": valuation.run,
    "sentiment": sentiment.run,
    # Phase B 新增
    "industry": industry.run,
    "macro": macro.run,
    "comparative": comparative.run,
    "dcf": dcf.run,
    "valuation_dcf": dcf.run,
    "profit": profit.run,
    "profit_breakdown": profit.run,
    # 统一综合报告（不区分财报/风险/估值/研报）
    "comprehensive": comprehensive.run,
    "report": comprehensive.run,
}


def run(stock_code: str, analysis_type: str, save: bool = True,
        stock_codes: list = None) -> dict:
    fn = DISPATCH.get(analysis_type)
    if not fn:
        return {"error": f"未知分析类型 {analysis_type}"}
    if stock_codes is not None:
        result = fn(stock_code, save=save, stock_codes=stock_codes)
    else:
        result = fn(stock_code, save=save)
    # 最外层统一注入 §6.5 免责声明（仅一次），保证所有分析输出均含合规声明
    if isinstance(result, dict) and "response" in result and not result.get("error"):
        result["response"] = attach_disclaimer(result["response"])
        result["disclaimer"] = True
    return result
