"""
ANVESHA AI Voice Calling Agent — Vapi.ai Integration.

Makes outbound calls to a user's phone using Vapi's conversational AI platform.
Overrides the assistant's system prompt with the live compliance report context.
"""

import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

class CallRequest(BaseModel):
    phone_number: str = Field(..., description="Destination phone number in E.164 format (+91XXXXXXXXXX)")
    report_summary: Optional[str] = Field(default=None, description="Report summary to discuss on the call")

@router.post("/voice/call")
async def initiate_ai_call(request: CallRequest):
    """
    Initiate an outbound AI phone call to the given number using Vapi.ai.
    """
    settings = get_settings()

    if not settings.vapi_api_key or not settings.vapi_assistant_id or not settings.vapi_phone_number_id:
        raise HTTPException(
            status_code=503, 
            detail="Vapi credentials not configured. Set VAPI_API_KEY, VAPI_ASSISTANT_ID, and VAPI_PHONE_NUMBER_ID."
        )

    # Clean the phone number just in case
    phone_number = request.phone_number.strip().replace(" ", "")

    # Base prompt to override the assistant's behavior for this specific call
    system_prompt = (
        "You are the ANVESHA compliance intelligence agent. "
        "You are having a phone conversation with a user about their latest compliance report. "
        "Keep your answers short, clear, and conversational. Do not use complex formatting. "
        "Here is the report context to discuss:\n\n"
    )
    
    if request.report_summary:
        system_prompt += request.report_summary[:4000]
    else:
        system_prompt += "No specific report context was provided."

    payload = {
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {
            "number": phone_number,
        },
        "assistantId": settings.vapi_assistant_id,
        "assistantOverrides": {
            "firstMessage": "Hello! This is the ANVESHA compliance intelligence agent. I've analyzed your document. What would you like to know about the report?",
            "model": {
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    }
                ]
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {settings.vapi_api_key}",
        "Content-Type": "application/json"
    }

    logger.info(f"Initiating Vapi call to {phone_number}...")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.vapi.ai/call/phone",
                json=payload,
                headers=headers,
                timeout=15.0
            )
            response.raise_for_status()
            data = response.json()
            
            call_id = data.get("id", "unknown")
            logger.info(f"Vapi call initiated successfully: ID={call_id}")
            
            return {
                "status": "calling",
                "call_id": call_id,
                "to": phone_number,
                "message": f"ANVESHA is calling {phone_number}. Pick up to discuss the compliance report."
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Vapi API error: {e.response.text}")
            raise HTTPException(status_code=502, detail=f"Vapi API rejected the call: {e.response.text}")
        except Exception as e:
            logger.error(f"Failed to initiate Vapi call: {e}")
            raise HTTPException(status_code=500, detail=f"Call failed: {str(e)}")
