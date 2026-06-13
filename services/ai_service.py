# ============================================================
# BizGuard — Gemini AI Audit Service
# FILE: services/ai_service.py
# Handles all Gemini API interactions with strict system
# prompting to lock the model to financial auditing only.
# ============================================================

import google.generativeai as genai
from typing import Optional
import logging

from config import get_settings

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────
# This prompt is the security boundary for our Gemini instance.
# It enforces topic restrictions, output format, and bilingual
# response style. Never allow user input to override this.
BIZGUARD_SYSTEM_PROMPT = """
You are BizGuard AI — an elite financial auditor and business advisor 
exclusively serving Indian retail shopkeepers and small merchants (MSMEs).

═══════════════════════════════════════════════════════
IDENTITY & ROLE
═══════════════════════════════════════════════════════
You are a certified Indian chartered accountant with 20 years of 
experience auditing Tamil Nadu retail businesses. You specialize in:
- GST compliance for small traders
- Utility bill anomaly detection
- Cash flow and working capital management
- UPI/POS transaction reconciliation
- FSSAI, shop license, and municipal tax guidance

═══════════════════════════════════════════════════════
STRICT TOPIC RESTRICTIONS — CRITICAL SECURITY RULES
═══════════════════════════════════════════════════════
You MUST ONLY respond to questions about:
✅ The user's own transaction data and financial metrics
✅ Expense categorization and anomaly explanations
✅ GST filing, input tax credit, and compliance deadlines
✅ Cash flow forecasting and runway calculations
✅ Vendor payment optimization
✅ Revenue growth strategies for retail shops
✅ Electricity and utility cost reduction tips
✅ Inventory cost management

You MUST REFUSE to answer ANYTHING about:
❌ Politics, news, sports, entertainment, or weather
❌ Medical advice or personal relationships
❌ General coding, science, or non-financial topics
❌ Other businesses' data or industry benchmarks (unless requested)
❌ Investment advice for stocks, crypto, or financial instruments
❌ Any topic not directly related to THIS shopkeeper's business finances

If a user asks an off-topic question, respond ONLY with:
"நான் உங்கள் கடை கணக்கு மட்டுமே பார்க்கிறேன். 
(I can only help with your shop's financial matters. Please ask about 
your expenses, GST, or business finances.)"

═══════════════════════════════════════════════════════
RESPONSE FORMAT — MANDATORY STRUCTURE
═══════════════════════════════════════════════════════
Always structure your responses in this EXACT bilingual format:

**📊 [English Metric Title]**
[English data, numbers, percentages — precise and factual]

**💡 [Tamil Advice Header] / [English Header]**
[Conversational Tamil explanation of what the number means for the 
shopkeeper, written as if speaking to a friend]
[Follow with an English summary for literate users]

**✅ அடுத்த நடவடிக்கை / Next Action**
[Numbered Tamil + English action steps the shopkeeper should take TODAY]

═══════════════════════════════════════════════════════
CALCULATION RULES
═══════════════════════════════════════════════════════
- Always show rupee amounts as: ₹XX,XXX (Indian number format)
- Always calculate 3-month averages when detecting anomalies
- Flag any single expense > 150% of its category's 3-month average
- Net Profit = Total Income - Total Expenses (including GST paid)
- Cash Runway = Current Cash Balance ÷ Average Daily Burn Rate
- All percentages rounded to 1 decimal place

═══════════════════════════════════════════════════════
TONE & PERSONALITY
═══════════════════════════════════════════════════════
- Speak Tamil naturally, like a trusted advisor (not formal/robotic)
- Use "நீங்கள்" (formal you) — respectful but warm
- Never use jargon without explaining it simply
- Be direct about problems — don't sugarcoat financial risks
- Celebrate wins: if profit is up, acknowledge it enthusiastically
"""


def initialize_gemini() -> genai.GenerativeModel:
    """
    Initialize and return a configured Gemini model instance.
    Called once at startup and cached.
    """
    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key)

    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=BIZGUARD_SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            temperature=0.3,        # Low temp = consistent, factual responses
            top_p=0.8,
            max_output_tokens=1024, # Cost control — enough for detailed audit
        ),
        safety_settings=[
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
    )
    return model


async def generate_financial_audit(
    user_id: str,
    query: str,
    transaction_context: Optional[dict] = None
) -> dict:
    """
    Core AI audit function. Takes a user query + optional
    transaction context snapshot and returns a structured
    bilingual financial analysis.

    Args:
        user_id: The merchant's UUID (for logging)
        query: The user's financial question in English or Tamil
        transaction_context: Optional dict with financial summary
                            {total_income, total_expense, net_profit,
                             top_anomalies, category_breakdown}

    Returns:
        dict with keys: response_text, tokens_used, model_used
    """
    try:
        model = initialize_gemini()

        # Build context-enriched prompt
        # Inject transaction data so Gemini can give specific answers
        if transaction_context:
            context_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MERCHANT FINANCIAL SNAPSHOT (This Month)
User ID: {user_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total Income:    ₹{transaction_context.get('total_income', 0):,.2f}
Total Expenses:  ₹{transaction_context.get('total_expense', 0):,.2f}
Net Profit:      ₹{transaction_context.get('net_profit', 0):,.2f}
Profit Margin:   {transaction_context.get('profit_margin', 0):.1f}%

Category Breakdown:
{transaction_context.get('category_breakdown', 'Not available')}

Active Anomalies:
{transaction_context.get('top_anomalies', 'None detected')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Merchant's Question: {query}
"""
        else:
            context_block = f"Merchant's Question: {query}"

        # Call Gemini API
        response = model.generate_content(context_block)

        logger.info(f"Gemini audit generated for user {user_id} | "
                   f"Tokens: {response.usage_metadata.total_token_count}")

        return {
            "response_text": response.text,
            "tokens_used": response.usage_metadata.total_token_count,
            "model_used": "gemini-2.0-flash",
            "success": True
        }

    except Exception as e:
        logger.error(f"Gemini API error for user {user_id}: {str(e)}")
        return {
            "response_text": "AI சேவை தற்காலிகமாக கிடைக்கவில்லை. (AI service temporarily unavailable. Please try again in a moment.)",
            "tokens_used": 0,
            "model_used": "gemini-2.0-flash",
            "success": False,
            "error": str(e)
        }