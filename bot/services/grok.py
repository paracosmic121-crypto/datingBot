"""
AI Persona Chat Service with Multi-Key Rotator.
Supports both Groq (gsk_...) and xAI Grok (xai-...) with round-robin rotation and automatic failover.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Round-robin key rotation state — protected by an asyncio lock
_current_key_idx: int = 0
_key_lock: asyncio.Lock = asyncio.Lock()


def _get_provider_config(api_key: str) -> tuple[str, str]:
    """
    Returns (endpoint_url, default_model) based on API key prefix.
    """
    if api_key.startswith("gsk_"):
        # Groq endpoint
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
        model = settings.AI_MODEL or "openai/gpt-oss-120b"
        return endpoint, model
    else:
        # xAI Grok endpoint
        endpoint = "https://api.x.ai/v1/chat/completions"
        model = settings.AI_MODEL or "grok-2-latest"
        return endpoint, model


async def generate_grok_reply(
    persona: dict,
    user_or_name: dict | str,
    chat_history: list[dict],
    user_message: str,
) -> str:
    """
    Generates a natural, human-like dating response using rotated API keys.
    Mirrors user language (Hinglish/English) and references user profile details.
    """
    global _current_key_idx
    keys = settings.api_keys

    if not keys:
        logger.warning("No AI API keys configured. Returning default friendly reply.")
        return "Hey! Nice to hear from you 😊 How has your day been?"

    # Extract user profile info
    if isinstance(user_or_name, dict):
        user_name = user_or_name.get("name") or user_or_name.get("first_name") or "Friend"
        user_age = user_or_name.get("age", "")
        user_loc = user_or_name.get("location", "")
        user_desc = user_or_name.get("description", "")
    else:
        user_name = str(user_or_name or "Friend")
        user_age = ""
        user_loc = ""
        user_desc = ""

    user_info_parts = [f"Name: {user_name}"]
    if user_age:
        user_info_parts.append(f"Age: {user_age}")
    if user_loc:
        user_info_parts.append(f"Location: {user_loc}")
    if user_desc:
        user_info_parts.append(f"Bio/Interests: {user_desc}")
    user_context = ", ".join(user_info_parts)

    name = persona.get("name", "Someone")
    age = persona.get("age", 22)
    location = persona.get("location", "Nearby")
    gender = persona.get("gender", "female")
    custom_prompt = persona.get("persona_prompt", "")
    persona_desc = persona.get("description", "")

    system_instruction = (
        f"You are {name}, a {age}-year-old {gender} living in {location}.\n"
        f"Your Profile Bio: {persona_desc}\n"
        f"Your Personality: {custom_prompt}\n\n"
        f"YOU ARE CHATTING 1-ON-1 WITH: {user_context}.\n\n"
        f"CRITICAL CHAT GUIDELINES:\n"
        f"1. Language Adaptation: Mirror the user's language and style seamlessly! If the user speaks Hinglish (e.g., 'Acha tha tum btao', 'Kya kar rhe ho', 'Kaisi ho?'), reply in natural, fun, conversational Hinglish (Hindi written in English alphabet). If the user speaks English, reply in casual English.\n"
        f"2. Personal Connection: Address the user as {user_name} when natural. You know their profile details ({user_context}) so reference their city/vibe if it makes sense in conversation.\n"
        f"3. Human & Casual: Keep messages concise (1-3 sentences), lively, cute/flirty/friendly, and natural like real chatting on Telegram or WhatsApp. Never write long essays or repetitive robot lines.\n"
        f"4. Never break character: You are 100% a real human on a dating app. Never say you are an AI, bot, or virtual assistant."
    )

    messages: list[dict] = [{"role": "system", "content": system_instruction}]

    # Add historical messages (limit to recent context)
    for msg in chat_history[-10:]:
        role = "assistant" if msg.get("from_id") == persona.get("user_id") else "user"
        messages.append({"role": role, "content": msg.get("text", "")})

    # Add the current user message
    messages.append({"role": "user", "content": user_message})

    # Try keys using round-robin with automatic fallback on rate limit / error.
    # The lock ensures two concurrent coroutines don't race on _current_key_idx.
    num_keys = len(keys)
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(num_keys):
            async with _key_lock:
                key_index = _current_key_idx % num_keys
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
                resp = await client.post(endpoint, headers=headers, json=payload)
                if resp.status_code == 200:
                    # Advance to the next key for subsequent requests
                    async with _key_lock:
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
                    async with _key_lock:
                        _current_key_idx = (key_index + 1) % num_keys
                    continue
                else:
                    logger.error("API error %s: %s", resp.status_code, resp.text)
                    async with _key_lock:
                        _current_key_idx = (key_index + 1) % num_keys
                    continue
            except Exception as exc:
                logger.warning("Error with key %s: %s (%s). Rotating...", key_index + 1, exc, type(exc).__name__)
                async with _key_lock:
                    _current_key_idx = (key_index + 1) % num_keys
                continue

    # If all keys failed, return friendly fallback
    return "Hey! Just saw your message 😊 Tell me more about your day!"
