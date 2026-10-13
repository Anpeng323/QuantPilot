from . import financial, risk, valuation, sentiment, industry, macro, comparative, dcf

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
}


def run(stock_code: str, analysis_type: str, save: bool = True,
        stock_codes: list = None) -> dict:
    fn = DISPATCH.get(analysis_type)
    if not fn:
        return {"error": f"未知分析类型 {analysis_type}"}
    if stock_codes is not None:
        return fn(stock_code, save=save, stock_codes=stock_codes)
    return fn(stock_code, save=save)
