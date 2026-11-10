import os
import asyncio
import sqlite3

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

# ==========================================
# 1. LOAD CONFIGURATION
# ==========================================

load_dotenv()

ESP32_URL = os.getenv(
    "ESP32_URL",
    "http://192.168.1.116"
).rstrip("/")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash-lite"
)

POLL_INTERVAL = int(
    os.getenv("POLL_INTERVAL", "30")
)

DB_NAME = "agrisense.db"


# ==========================================
# 2. DATABASE INITIALIZATION
# ==========================================

def init_database():

    conn = sqlite3.connect(DB_NAME)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_history (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            timestamp TEXT,

            soil_moisture REAL,

            temperature REAL,

            humidity REAL,

            rain_detected INTEGER,

            water_available INTEGER,

            soil_raw INTEGER,

            rain_raw INTEGER,

            water_raw INTEGER
        )
    """)

    conn.commit()
    conn.close()

    print("Database initialized successfully.")


# ==========================================
# 3. SAVE SENSOR DATA
# ==========================================

def save_sensor_data(data):

    conn = sqlite3.connect(DB_NAME)

    conn.execute("""
        INSERT INTO sensor_history (

            timestamp,
            soil_moisture,
            temperature,
            humidity,
            rain_detected,
            water_available,
            soil_raw,
            rain_raw,
            water_raw

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (

        datetime.now(timezone.utc).isoformat(),

        data.get("soil_moisture"),

        data.get("temperature"),

        data.get("humidity"),

        int(data.get("rain_detected", False)),

        int(data.get("water_available", False)),

        data.get("soil_raw"),

        data.get("rain_raw"),

        data.get("water_raw")
    ))

    conn.commit()
    conn.close()


# ==========================================
# 4. GET ESP32 SENSOR DATA
# ==========================================

async def get_esp32_data():

    url = f"{ESP32_URL}/api/data"

    async with httpx.AsyncClient(timeout=5) as client:

        response = await client.get(url)

        response.raise_for_status()

        data = response.json()

        required_fields = [
            "soil_moisture",
            "rain_detected",
            "water_available",
            "pump_on"
        ]

        for field in required_fields:

            if field not in data:

                raise ValueError(
                    f"Missing ESP32 field: {field}"
                )

        return data


# ==========================================
# 5. AUTOMATIC SENSOR DATA COLLECTION
# ==========================================

async def sensor_collection_loop():

    while True:

        try:

            data = await get_esp32_data()

            save_sensor_data(data)

            print("Sensor data saved successfully.")

        except Exception as error:

            print("Sensor collection error:", error)

        await asyncio.sleep(POLL_INTERVAL)


# ==========================================
# 6. FASTAPI LIFESPAN
# ==========================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    init_database()

    task = asyncio.create_task(
        sensor_collection_loop()
    )

    print("AgriSense AI backend started.")

    yield

    task.cancel()

    try:

        await task

    except asyncio.CancelledError:

        pass


# ==========================================
# 7. FASTAPI APPLICATION
# ==========================================

app = FastAPI(

    title="AgriSense AI Smart Irrigation",

    description="AI-powered agriculture monitoring system",

    version="1.0",

    lifespan=lifespan
)


# ==========================================
# 8. HOME ENDPOINT
# ==========================================

@app.get("/")
def home():

    return {

        "project": "AgriSense AI",

        "backend": "Running",

        "ai_model": GEMINI_MODEL,

        "message": "Welcome to AgriSense AI"
    }


# ==========================================
# 9. HEALTH CHECK
# ==========================================

@app.get("/health")
def health():

    return {

        "status": "healthy",

        "backend": "online"
    }


# ==========================================
# 10. LIVE SENSOR STATUS
# ==========================================

@app.get("/status")
async def status():

    try:

        data = await get_esp32_data()

        save_sensor_data(data)

        return {

            "connection": "ESP32 Connected",

            "sensors": data
        }

    except Exception as error:

        raise HTTPException(

            status_code=503,

            detail=str(error)
        )


# ==========================================
# 11. SENSOR HISTORY
# ==========================================

@app.get("/history")
def history():

    conn = sqlite3.connect(DB_NAME)

    conn.row_factory = sqlite3.Row

    rows = conn.execute("""

        SELECT *

        FROM sensor_history

        ORDER BY id DESC

        LIMIT 50

    """).fetchall()

    conn.close()

    return {

        "total_records": len(rows),

        "history": [dict(row) for row in rows]
    }


# ==========================================
# 12. GEMINI AI FARMING ADVICE
# ==========================================

@app.get("/advice")
async def farming_advice():

    if not GEMINI_API_KEY:

        raise HTTPException(

            status_code=500,

            detail="Gemini API key missing in .env"
        )

    try:

        sensor_data = await get_esp32_data()

        prompt = f"""
You are AgriSense AI, an agricultural assistant.

Analyze the following live sensor readings:

Soil moisture: {sensor_data.get('soil_moisture')}%

Temperature: {sensor_data.get('temperature')} C

Humidity: {sensor_data.get('humidity')}%

Rain detected: {sensor_data.get('rain_detected')}

Water available: {sensor_data.get('water_available')}

Provide:

1. Current field condition.
2. Soil moisture interpretation.
3. Temperature and humidity interpretation.
4. Rain and water availability interpretation.
5. Recommended next steps.

Important rules:

- Do not claim irrigation is safe if water is unavailable.
- Soil moisture calibration may not be accurate.
- Do not directly control the pump.
- Clearly explain uncertainty.
- Use simple language for farmers.
"""

        url = (

            "https://generativelanguage.googleapis.com/"

            f"v1beta/models/{GEMINI_MODEL}:generateContent"

        )

        headers = {

            "x-goog-api-key": GEMINI_API_KEY

        }

        body = {

            "contents": [

                {

                    "parts": [

                        {

                            "text": prompt

                        }

                    ]

                }

            ]

        }

        async with httpx.AsyncClient(timeout=45) as client:

            response = await client.post(

                url,

                headers=headers,

                json=body

            )

            response.raise_for_status()

            result = response.json()

        answer = result["candidates"][0][
            "content"
        ]["parts"][0]["text"]

        return {

            "ai_model": GEMINI_MODEL,

            "sensor_data": sensor_data,

            "advice": answer

        }

    except httpx.HTTPStatusError as error:

        raise HTTPException(

            status_code=502,

            detail=f"Gemini API error: {error.response.text}"

        )

    except Exception as error:

        raise HTTPException(

            status_code=500,

            detail=str(error)

        )
