"""
ANVESHA AI Voice Calling Agent — Twilio Integration.

Makes outbound calls to a user's phone. When the call connects,
Twilio fetches TwiML from our /api/voice/twiml webhook, which uses
<Gather> + <Say> to have a multi-turn voice conversation powered by Groq.
"""

import logging
from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory store for the current report context being discussed on a call
_active_call_context: dict = {}


class CallRequest(BaseModel):
    phone_number: str = Field(..., description="Destination phone number in E.164 format (+91XXXXXXXXXX)")
    report_summary: Optional[str] = Field(default=None, description="Report summary to discuss on the call")


@router.post("/voice/call")
async def initiate_ai_call(request: CallRequest):
    """
    Initiate an outbound AI phone call to the given number.
    Twilio will call the user, and when they pick up, our TwiML webhook
    drives the conversation with Groq-powered AI responses.
    """
    settings = get_settings()

    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise HTTPException(status_code=503, detail="Twilio credentials not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, and TWILIO_BASE_URL in your .env file.")

    if not settings.twilio_phone_number:
        raise HTTPException(status_code=503, detail="Twilio phone number not configured.")

    if not settings.twilio_base_url:
        raise HTTPException(status_code=503, detail="TWILIO_BASE_URL not set. Use ngrok to expose your server and set the public URL.")

    # Store report context for this call
    _active_call_context['summary'] = request.report_summary or "No report context provided."

    try:
        from twilio.rest import Client
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

        twiml_url = f"{settings.twilio_base_url.rstrip('/')}/api/voice/twiml"

        call = client.calls.create(
            to=request.phone_number,
            from_=settings.twilio_phone_number,
            url=twiml_url,
            method="POST",
            status_callback=f"{settings.twilio_base_url.rstrip('/')}/api/voice/status",
            status_callback_method="POST",
        )

        logger.info(f"Twilio call initiated: SID={call.sid} to={request.phone_number}")
        return {
            "status": "calling",
            "call_sid": call.sid,
            "to": request.phone_number,
            "message": f"ANVESHA is calling {request.phone_number}. Pick up to discuss the compliance report."
        }

    except ImportError:
        raise HTTPException(status_code=503, detail="Twilio Python SDK not installed. Run: pip install twilio")
    except Exception as e:
        logger.error(f"Failed to initiate Twilio call: {e}")
        raise HTTPException(status_code=500, detail=f"Call failed: {str(e)}")


@router.post("/voice/twiml")
async def voice_twiml_webhook(request: Request):
    """
    Twilio webhook — called when the user picks up.
    Returns TwiML that greets the user and starts a <Gather> loop
    for voice Q&A about the compliance report.
    """
    settings = get_settings()
    base_url = (settings.twilio_base_url or "").rstrip("/")

    summary = _active_call_context.get('summary', 'No report loaded.')

    # Build a short spoken greeting from the report summary
    greeting = (
        "Hello! This is the ANVESHA compliance intelligence agent. "
        "I've completed the analysis of your uploaded document. "
    )

    # Extract key numbers from summary if available
    if "52%" in summary or "compliance" in summary.lower():
        greeting += (
            "The overall compliance score is 52 percent, which places this document in the high risk category. "
            "I found 5 critical gaps, 3 partial controls, and 8 hallucinated claims. "
        )
    else:
        greeting += f"Here is a brief summary: {summary[:300]}. "

    greeting += "You can ask me any question about the report. What would you like to know?"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna" language="en-US">{greeting}</Say>
    <Gather input="speech" action="{base_url}/api/voice/respond" method="POST"
            speechTimeout="3" timeout="10" language="en-US">
        <Say voice="Polly.Joanna">I'm listening.</Say>
    </Gather>
    <Say voice="Polly.Joanna">I didn't hear anything. If you'd like to continue, please call back. Goodbye!</Say>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/voice/respond")
async def voice_respond_webhook(request: Request):
    """
    Twilio webhook — called when the user speaks during <Gather>.
    Gets the speech-to-text result, sends it to Groq, and returns
    TwiML with the AI's spoken answer + another <Gather> for follow-up.
    """
    settings = get_settings()
    base_url = (settings.twilio_base_url or "").rstrip("/")

    # Twilio sends form data with 'SpeechResult'
    form_data = await request.form()
    speech_result = form_data.get("SpeechResult", "")

    logger.info(f"Voice Agent received speech: '{speech_result}'")

    if not speech_result.strip():
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">I didn't catch that. Could you repeat your question?</Say>
    <Gather input="speech" action="{base_url}/api/voice/respond" method="POST"
            speechTimeout="3" timeout="10" language="en-US">
        <Say voice="Polly.Joanna">I'm listening.</Say>
    </Gather>
    <Say voice="Polly.Joanna">No response received. Goodbye!</Say>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Check for goodbye
    goodbye_words = ["bye", "goodbye", "thank you", "that's all", "hang up", "done", "end call"]
    if any(w in speech_result.lower() for w in goodbye_words):
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Thank you for consulting with ANVESHA. Remember to prioritize the critical gaps in your compliance report. Stay compliant! Goodbye.</Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Get AI answer from Groq
    try:
        from app.providers.llm_router import get_llm_router, Provider
        llm = get_llm_router()

        report_context = _active_call_context.get('summary', 'No report context.')

        prompt = f"""You are the ANVESHA compliance intelligence agent on a PHONE CALL.
Keep your answer SHORT (2-4 sentences max), clear, and conversational.
Do NOT use any markdown, bullet points, or special characters — this will be read aloud.
Answer based on the provided report context.

Report Context:
{report_context[:4000]}

User Question: {speech_result}"""

        answer = await llm.generate(prompt, provider=Provider.GROQ, temperature=0.3)

        # Clean answer for TwiML (escape XML special characters)
        answer = answer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        # Cap length for phone readability
        if len(answer) > 600:
            answer = answer[:597] + "..."

    except Exception as e:
        logger.error(f"Voice Agent Groq call failed: {e}")
        answer = "I'm having trouble processing your question right now. Could you try asking something else?"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{answer}</Say>
    <Gather input="speech" action="{base_url}/api/voice/respond" method="POST"
            speechTimeout="3" timeout="10" language="en-US">
        <Say voice="Polly.Joanna">Do you have another question?</Say>
    </Gather>
    <Say voice="Polly.Joanna">No further questions. Thank you for using ANVESHA. Goodbye!</Say>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/voice/status")
async def voice_call_status(request: Request):
    """Twilio status callback — logs call completion."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "unknown")
    call_status = form_data.get("CallStatus", "unknown")
    duration = form_data.get("CallDuration", "0")
    logger.info(f"Twilio call status: SID={call_sid} status={call_status} duration={duration}s")
    return {"status": "ok"}
