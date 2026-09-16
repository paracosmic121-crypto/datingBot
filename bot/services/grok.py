"""
AI Persona Chat Service with Multi-Key Rotator.
Supports both Groq (gsk_...) and xAI Grok (xai-...) with round-robin rotation and automatic failover.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Atomic key index for round-robin rotation
_current_key_idx: int = 0


def _get_provider_config(api_key: str) -> tuple[str, str]:
    """
    Returns (endpoint_url, default_model) based on API key prefix.
    """
    if api_key.startswith("gsk_"):
        # Groq endpoint
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
        model = settings.AI_MODEL or "llama-3.3-70b-versatile"
        return endpoint, model
    else:
        # xAI Grok endpoint
        endpoint = "https://api.x.ai/v1/chat/completions"
        model = settings.AI_MODEL or "grok-2-latest"
        return endpoint, model


async def generate_grok_reply(
    persona: dict,
    user_name: str,
    chat_history: list[dict],
    user_message: str,
) -> str:
    """
    Generates a natural, human-like dating response using rotated API keys.
    """
    global _current_key_idx
    keys = settings.api_keys

    if not keys:
        logger.warning("No AI API keys configured. Returning default friendly reply.")
        return "Hey! Nice to hear from you 😊 How has your day been?"

    name = persona.get("name", "Someone")
    age = persona.get("age", 22)
    location = persona.get("location", "Nearby")
    gender = persona.get("gender", "female")
    custom_prompt = persona.get("persona_prompt")

    default_system = (
        f"You are {name}, a {age}-year-old {gender} living in {location}. "
        f"You are chatting on a fun, casual dating bot with {user_name}. "
        f"Personality: warm, playful, charming, authentic, and engaging. "
        f"Keep responses natural, short to medium length (1-3 sentences max), like real messaging on Telegram. "
        f"Never say you are an AI, a language model, or a bot. Act 100% human. "
        f"Occasionally use light emojis. Respond in the same language or tone (English or Hinglish) the user speaks."
    )

    system_instruction = custom_prompt if custom_prompt else default_system

    messages: list[dict] = [{"role": "system", "content": system_instruction}]

    # Add historical messages (limit to recent context)
    for msg in chat_history[-10:]:
        role = "assistant" if msg.get("from_id") == persona.get("user_id") else "user"
        messages.append({"role": role, "content": msg.get("text", "")})

    # Add the current user message
    messages.append({"role": "user", "content": user_message})

    # Try keys using round-robin with automatic fallback on rate limit / error
    num_keys = len(keys)
    for attempt in range(num_keys):
        key_index = (_current_key_idx + attempt) % num_keys
        api_key = keys[key_index]
        endpoint, model = _get_provider_config(api_key)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 150,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(endpoint, headers=headers, json=payload)
                if resp.status_code == 200:
                    # Advance global index to next key for the subsequent request
                    _current_key_idx = (key_index + 1) % num_keys
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                    return reply
                elif resp.status_code in (429, 401):
                    logger.warning(
                        "Key %s (masked ...%s) returned status %s. Rotating to next key...",
                        key_index + 1,
                        api_key[-6:],
                        resp.status_code,
                    )
                    continue
                else:
                    logger.error("API error %s: %s", resp.status_code, resp.text)
                    continue
        except Exception as exc:
            logger.warning("Error with key %s: %s. Rotating...", key_index + 1, exc)
            continue

    # If all keys failed, advance index and return friendly fallback
    _current_key_idx = (_current_key_idx + 1) % num_keys
    return "Hey! Just saw your message 😊 Tell me more about your day!"
