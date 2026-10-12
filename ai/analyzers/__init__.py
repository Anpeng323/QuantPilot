from . import financial, risk, valuation, sentiment

DISPATCH = {
    "financial": financial.run,
    "financial_summary": financial.run,
    "risk": risk.run,
    "risk_scan": risk.run,
    "valuation": valuation.run,
    "sentiment": sentiment.run,
}

def run(stock_code: str, analysis_type: str, save: bool = True) -> dict:
    fn = DISPATCH.get(analysis_type)
    if not fn:
        return {"error": f"未知分析类型 {analysis_type}"}
    return fn(stock_code, save=save)
