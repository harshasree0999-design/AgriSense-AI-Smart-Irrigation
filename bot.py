
import os
import re
import base64
import wave
import asyncio
import tempfile
import subprocess

from io import BytesIO

import httpx
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =====================================================
# 1. CONFIGURATION
# =====================================================

load_dotenv()

# Full FFmpeg path for Windows
FFMPEG_EXE = os.getenv(
    "FFMPEG_EXE",
    r"C:\Users\harsh\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL", "gemini-2.5-flash-lite"
)

GEMINI_TTS_MODEL = os.getenv(
    "GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"
)

BACKEND_URL = os.getenv(
    "BACKEND_URL", "http://127.0.0.1:8000"
).rstrip("/")

VOICE_REPLY = os.getenv(
    "VOICE_REPLY", "true"
).lower() == "true"

ALLOWED_TELEGRAM_USER_ID = os.getenv(
    "ALLOWED_TELEGRAM_USER_ID", ""
).strip()

MAX_HISTORY = 12

GEMINI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
)

chat_history = {}

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing from .env")


# =====================================================
# 2. AUTHORIZATION
# =====================================================

def is_authorized(update: Update) -> bool:

    if not ALLOWED_TELEGRAM_USER_ID:
        return True

    user = update.effective_user

    return (
        user is not None
        and str(user.id) == ALLOWED_TELEGRAM_USER_ID
    )


async def reject_unauthorized(update: Update) -> bool:

    if is_authorized(update):
        return False

    if update.effective_message:
        await update.effective_message.reply_text(
            "You are not authorized to use this bot."
        )

    return True


# =====================================================
# 3. GEMINI CHAT API
# =====================================================

async def ask_gemini(contents, sensor_data=None):

    system_instruction = """
You are AgriSense AI, a friendly bilingual AI assistant.

You can answer general questions and questions about
agriculture, electronics, programming and smart irrigation.

LANGUAGE RULES:

1. If the user writes or speaks English, answer in English.
2. If the user writes or speaks Telugu, answer in Telugu.
3. If the user mixes English and Telugu, respond naturally
   using the same language style.
4. Do not translate unless the user asks.
5. Use simple and understandable language.
6. Give step-by-step explanations when useful.

AGRISENSE SAFETY:

1. Explain sensor readings when asked.
2. Do not claim that the pump is physically running
   just because the relay status says ON.
3. Never send pump-control commands.
4. Do not assume sensor calibration is correct.
5. Do not invent unavailable sensor values.
"""

    if sensor_data is not None:

        system_instruction += (
            "\n\nLATEST AGRISENSE SENSOR DATA:\n"
            + str(sensor_data)
            + "\nUse these readings only when relevant."
        )

    payload = {
        "systemInstruction": {
            "parts": [
                {"text": system_instruction}
            ]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1200
        }
    }

    url = (
        f"{GEMINI_BASE_URL}"
        f"{GEMINI_MODEL}:generateContent"
    )

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=90) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload
        )

        response.raise_for_status()

        result = response.json()

    candidates = result.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            f"Gemini returned no answer: {result}"
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    answer = "\n".join(
        part["text"]
        for part in parts
        if part.get("text")
    ).strip()

    if not answer:
        raise RuntimeError("Gemini returned an empty answer.")

    return answer


# =====================================================
# 4. GET ESP32 SENSOR DATA
# =====================================================

async def get_sensor_data():

    try:

        async with httpx.AsyncClient(timeout=15) as client:

            response = await client.get(
                f"{BACKEND_URL}/status"
            )

            response.raise_for_status()

            result = response.json()

        return result.get("sensors", {})

    except Exception as error:

        print("Sensor API error:", error)

        return None


def format_status(data):

    if not data:

        return (
            "AgriSense sensor data is unavailable.\n"
            "Please check FastAPI and ESP32."
        )

    return (
        "🌱 AGRISENSE AI - LIVE STATUS\n\n"
        f"🌡 Temperature: {data.get('temperature', 'N/A')} °C\n"
        f"💧 Humidity: {data.get('humidity', 'N/A')} %\n"
        f"🌱 Soil moisture: {data.get('soil_moisture', 'N/A')} %\n"
        f"🌧 Rain detected: {data.get('rain_detected', 'N/A')}\n"
        f"🚰 Water available: {data.get('water_available', 'N/A')}\n"
        f"⚙ Pump relay: {data.get('pump_on', 'N/A')}\n"
        f"🔄 Pump mode: {data.get('pump_mode', 'N/A')}\n"
        f"📋 Irrigation: {data.get('irrigation_status', 'N/A')}\n"
        f"📡 ESP32 IP: {data.get('ip', 'N/A')}\n"
        f"📶 Wi-Fi RSSI: {data.get('rssi', 'N/A')} dBm\n\n"
        "Note: Relay status does not verify actual water flow."
    )


# =====================================================
# 5. GEMINI TEXT-TO-SPEECH
# =====================================================

async def generate_voice_reply(text):

    url = (
        f"{GEMINI_BASE_URL}"
        f"{GEMINI_TTS_MODEL}:generateContent"
    )

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Speak the following answer naturally "
                            "and clearly. Preserve the original "
                            "language. If it is Telugu, speak Telugu. "
                            "If it is English, speak English. "
                            "Do not translate or add extra words.\n\n"
                            + text
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": "Kore"
                    }
                }
            }
        }
    }

    async with httpx.AsyncClient(timeout=120) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload
        )

        response.raise_for_status()

        result = response.json()

    candidates = result.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            f"Gemini TTS returned no candidates: {result}"
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    audio_data = None
    mime_type = ""

    for part in parts:

        inline_data = (
            part.get("inlineData")
            or part.get("inline_data")
        )

        if inline_data and inline_data.get("data"):

            audio_data = base64.b64decode(
                inline_data["data"]
            )

            mime_type = (
                inline_data.get("mimeType")
                or inline_data.get("mime_type")
                or ""
            )

            break

    if not audio_data:
        raise RuntimeError(
            "Gemini did not return audio data."
        )

    # -----------------------------------------------
    # CONVERT GEMINI AUDIO TO TELEGRAM OGG/OPUS
    # -----------------------------------------------

    with tempfile.TemporaryDirectory() as temp_dir:

        wav_path = os.path.join(
            temp_dir, "reply.wav"
        )

        ogg_path = os.path.join(
            temp_dir, "reply.ogg"
        )

        # If Gemini returned a complete WAV file,
        # use it directly.
        if audio_data[:4] == b"RIFF":

            with open(wav_path, "wb") as file:
                file.write(audio_data)

        else:

            # Gemini TTS PCM output is normally
            # 24 kHz, mono, 16-bit audio.
            rate_match = re.search(
                r"rate=(\d+)",
                mime_type
            )

            sample_rate = (
                int(rate_match.group(1))
                if rate_match
                else 24000
            )

            # Wrap PCM bytes in a WAV container.
            with wave.open(wav_path, "wb") as wav_file:

                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)

                wav_file.writeframes(audio_data)

        if not os.path.isfile(FFMPEG_EXE):
            raise FileNotFoundError(
                "FFmpeg executable was not found at: " + FFMPEG_EXE
            )

        command = [
            FFMPEG_EXE,
            "-y",
            "-i", wav_path,
            "-c:a", "libopus",
            "-b:a", "48k",
            "-vbr", "on",
            "-application", "voip",
            ogg_path
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:

            raise RuntimeError(
                "FFmpeg conversion failed: "
                + stderr.decode(
                    errors="replace"
                )[-1500:]
            )

        with open(ogg_path, "rb") as file:

            audio_file = BytesIO(file.read())

    audio_file.name = "agrisense_reply.ogg"

    return audio_file


# =====================================================
# 6. SEND TEXT AND VOICE
# =====================================================

async def send_ai_reply(update, answer):

    message = update.effective_message

    # Telegram text messages have a 4096-character limit.
    for start in range(0, len(answer), 4000):

        await message.reply_text(
            answer[start:start + 4000]
        )

    if not VOICE_REPLY:
        return

    try:

        audio_file = await generate_voice_reply(answer)

        await message.reply_voice(
            voice=audio_file,
            caption="🔊 AgriSense AI Voice Reply"
        )

    except Exception as error:

        print("Voice generation error:", error)

        await message.reply_text(
            "⚠️ Text reply was sent, but voice generation failed. "
            "Please check the terminal error."
        )


# =====================================================
# 7. SAVE CONVERSATION HISTORY
# =====================================================

def save_history(user_id, user_text, answer):

    history = chat_history.setdefault(
        user_id, []
    )

    history.append({
        "role": "user",
        "parts": [
            {"text": user_text}
        ]
    })

    history.append({
        "role": "model",
        "parts": [
            {"text": answer}
        ]
    })

    chat_history[user_id] = history[-MAX_HISTORY:]


# =====================================================
# 8. NORMAL TEXT MESSAGE
# =====================================================

async def handle_chat(update, context):

    if await reject_unauthorized(update):
        return

    user_id = str(update.effective_user.id)

    user_text = update.effective_message.text.strip()

    if not user_text:
        return

    try:

        history = chat_history.get(
            user_id, []
        )

        contents = history + [
            {
                "role": "user",
                "parts": [
                    {"text": user_text}
                ]
            }
        ]

        sensor_data = await get_sensor_data()

        answer = await ask_gemini(
            contents,
            sensor_data
        )

        save_history(
            user_id,
            user_text,
            answer
        )

        await send_ai_reply(
            update,
            answer
        )

    except httpx.HTTPStatusError as error:

        print("Gemini HTTP error:", error.response.text)

        await update.effective_message.reply_text(
            "Gemini API error. Please check the API key, "
            "model name and API access."
        )

    except Exception as error:

        print("Chat error:", error)

        await update.effective_message.reply_text(
            "Sorry, I could not process your message. "
            "Please check the terminal."
        )


# =====================================================
# 9. TELEGRAM VOICE MESSAGE
# =====================================================

async def handle_voice(update, context):

    if await reject_unauthorized(update):
        return

    user_id = str(update.effective_user.id)

    try:

        voice = update.effective_message.voice

        await update.effective_message.reply_text(
            "🎤 Voice received! Understanding your message..."
        )

        # Download Telegram voice note.
        telegram_file = await context.bot.get_file(
            voice.file_id
        )

        voice_bytes = await telegram_file.download_as_bytearray()

        audio_base64 = base64.b64encode(
            bytes(voice_bytes)
        ).decode("utf-8")

        # Ask Gemini to transcribe English or Telugu.
        transcription_prompt = """
Listen carefully to the attached voice recording.

The speaker may use English, Telugu, or both.

Transcribe exactly what the speaker said.
Preserve the original language and script.
Do not translate.
Do not answer the question.
Return only the transcription.
"""

        transcription_contents = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": transcription_prompt
                    },
                    {
                        "inlineData": {
                            "mimeType": "audio/ogg",
                            "data": audio_base64
                        }
                    }
                ]
            }
        ]

        transcript = await ask_gemini(
            transcription_contents
        )

        if not transcript.strip():
            raise RuntimeError(
                "Could not transcribe voice message."
            )

        print("Recognized voice:", transcript)

        # Use recognized words as the user's message.
        history = chat_history.get(
            user_id, []
        )

        contents = history + [
            {
                "role": "user",
                "parts": [
                    {"text": transcript}
                ]
            }
        ]

        sensor_data = await get_sensor_data()

        answer = await ask_gemini(
            contents,
            sensor_data
        )

        save_history(
            user_id,
            transcript,
            answer
        )

        await send_ai_reply(
            update,
            answer
        )

    except httpx.HTTPStatusError as error:

        print("Voice Gemini HTTP error:", error.response.text)

        await update.effective_message.reply_text(
            "Gemini API error while processing your voice. "
            "Check the model and API access."
        )

    except Exception as error:

        print("Voice handling error:", error)

        await update.effective_message.reply_text(
            "Sorry, I could not process your voice message. "
            "Please try again or send text."
        )


# =====================================================
# 10. TELEGRAM COMMANDS
# =====================================================

async def start(update, context):

    if await reject_unauthorized(update):
        return

    await update.effective_message.reply_text(
        "🌱 Welcome to AgriSense AI!\n\n"
        "I am your English and Telugu AI assistant.\n\n"
        "You can:\n"
        "💬 Send text messages\n"
        "🎤 Send voice messages\n"
        "🔊 Receive text and voice replies\n"
        "🌡 Check live ESP32 readings\n"
        "🌱 Ask agricultural questions\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Help\n"
        "/status - Live sensor readings\n"
        "/advice - Irrigation advice\n"
        "/history - Recent conversation\n"
        "/clear - Clear conversation"
    )


async def help_command(update, context):

    await start(update, context)


async def status_command(update, context):

    if await reject_unauthorized(update):
        return

    data = await get_sensor_data()

    await update.effective_message.reply_text(
        format_status(data)
    )


async def advice_command(update, context):

    if await reject_unauthorized(update):
        return

    try:

        data = await get_sensor_data()

        if not data:

            await update.effective_message.reply_text(
                "Sensor data unavailable. "
                "Check FastAPI and ESP32."
            )

            return

        prompt = """
Analyze these AgriSense sensor readings.

Explain:
1. Soil condition.
2. Water availability.
3. Rain condition.
4. Temperature and humidity.
5. Recommended irrigation action.

Use simple English.
Do not control the pump.
Do not claim that the pump is physically working.
Mention possible sensor calibration limitations.
"""

        answer = await ask_gemini(
            [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            data
        )

        await send_ai_reply(
            update,
            answer
        )

    except Exception as error:

        print("Advice error:", error)

        await update.effective_message.reply_text(
            "Could not generate irrigation advice. "
            "Please check the terminal."
        )


async def history_command(update, context):

    if await reject_unauthorized(update):
        return

    user_id = str(update.effective_user.id)

    history = chat_history.get(
        user_id, []
    )

    if not history:

        await update.effective_message.reply_text(
            "No conversation history yet."
        )

        return

    lines = ["📝 RECENT CONVERSATION\n"]

    for item in history[-8:]:

        role = (
            "You"
            if item["role"] == "user"
            else "AgriSense AI"
        )

        text = item["parts"][0].get(
            "text", ""
        )

        lines.append(
            f"{role}: {text}\n"
        )

    result = "\n".join(lines)

    for start in range(0, len(result), 4000):

        await update.effective_message.reply_text(
            result[start:start + 4000]
        )


async def clear_command(update, context):

    if await reject_unauthorized(update):
        return

    user_id = str(update.effective_user.id)

    chat_history.pop(user_id, None)

    await update.effective_message.reply_text(
        "🧹 Conversation history cleared!"
    )


# =====================================================
# 11. MAIN PROGRAM
# =====================================================

def main():

    print("======================================")
    print("       AGRISENSE AI TELEGRAM BOT")
    print("======================================")

    print("Chat model:", GEMINI_MODEL)
    print("TTS model:", GEMINI_TTS_MODEL)
    print("Voice replies:", VOICE_REPLY)
    print("Backend:", BACKEND_URL)

    print("======================================")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    application.add_handler(
        CommandHandler("advice", advice_command)
    )

    application.add_handler(
        CommandHandler("history", history_command)
    )

    application.add_handler(
        CommandHandler("clear", clear_command)
    )

    # Normal text messages
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_chat
        )
    )

    # Telegram voice messages
    application.add_handler(
        MessageHandler(
            filters.VOICE,
            handle_voice
        )
    )

    print("AgriSense AI bot is running...")

    application.run_polling()


if __name__ == "__main__":
    main()
