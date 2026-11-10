#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>
#include <time.h>
#include <math.h>
#include "secrets.h"

// ==================================================
// 1. WIFI CONFIGURATION
// ==================================================


// ==================================================
// 2. SENSOR PINS
// ==================================================

#define SOIL_PIN 34
#define RAIN_PIN 35
#define WATER_PIN 32
#define DHT_PIN 4

// ==================================================
// 3. RELAY CONFIGURATION
// ==================================================

#define RELAY_PIN 26

const int RELAY_ON = LOW;    // Active LOW relay
const int RELAY_OFF = HIGH;

// ==================================================
// 4. DHT22 CONFIGURATION
// ==================================================

#define DHT_TYPE DHT22

DHT dht(DHT_PIN, DHT_TYPE);

// ==================================================
// 5. WEB SERVER
// ==================================================

WebServer server(80);

bool serverStarted = false;

// ==================================================
// 6. SOIL CALIBRATION
// ==================================================

const int DRY_VALUE = 4095;
const int WET_VALUE = 2280;

const float MOISTURE_THRESHOLD = 35.0;

// ==================================================
// 7. WATER SENSOR CONFIGURATION
// ==================================================

// Provisional settings: verify with actual sensor readings.

const int WATER_THRESHOLD = 1000;

const bool WATER_VALUE_HIGH_WHEN_WET = true;

// ==================================================
// 8. RAIN SENSOR CONFIGURATION
// ==================================================

// Provisional settings: calibrate using dry and wet readings.

const int RAIN_THRESHOLD = 1800;

// true means lower analog reading indicates rain.
const bool RAIN_VALUE_LOW_WHEN_WET = true;

// ==================================================
// 9. PUMP SAFETY SETTINGS
// ==================================================

const unsigned long MAX_PUMP_RUNTIME = 10000;  // 10 seconds

const unsigned long PUMP_COOLDOWN = 30000;     // 30 seconds

// ==================================================
// 10. SENSOR VARIABLES
// ==================================================

int soilValue = 0;
int rainValue = 0;
int waterValue = 0;

float moisture = 0;
float temperature = 0;
float humidity = 0;

bool dhtError = true;
bool waterAvailable = false;
bool rainDetected = false;

bool pumpOn = false;

// ==================================================
// 11. PUMP OPERATING MODE
// ==================================================

enum PumpMode {
  AUTO_MODE,
  MANUAL_ON_MODE,
  MANUAL_OFF_MODE
};

PumpMode pumpMode = AUTO_MODE;

unsigned long sensorTimer = 0;
unsigned long pumpStartedAt = 0;
unsigned long lastPumpStopAt = 0;

bool hasPumpRun = false;

const unsigned long SENSOR_INTERVAL = 2500;

String irrigationStatus = "Starting system";

// ==================================================
// 12. HELPER FUNCTIONS
// ==================================================

String getPumpMode() {

  if (pumpMode == AUTO_MODE) {
    return "AUTO";
  }

  if (pumpMode == MANUAL_ON_MODE) {
    return "MANUAL_ON";
  }

  return "MANUAL_OFF";
}

void stopPump(String reason) {

  digitalWrite(RELAY_PIN, RELAY_OFF);

  if (pumpOn) {
    lastPumpStopAt = millis();
    hasPumpRun = true;
  }

  pumpOn = false;

  irrigationStatus = reason;
}

bool safetyConditionsOK() {

  if (!waterAvailable) {
    irrigationStatus = "Pump blocked: water unavailable";
    return false;
  }

  if (rainDetected) {
    irrigationStatus = "Pump blocked: rain detected";
    return false;
  }

  return true;
}

bool cooldownFinished() {

  if (!hasPumpRun) {
    return true;
  }

  return (millis() - lastPumpStopAt >= PUMP_COOLDOWN);
}

void startPump() {

  digitalWrite(RELAY_PIN, RELAY_ON);

  pumpOn = true;
  pumpStartedAt = millis();

  irrigationStatus = "Pump running";

  Serial.println("PUMP ON");
}

// ==================================================
// 13. WIFI INFORMATION
// ==================================================

void printWiFiInfo() {

  if (WiFi.status() == WL_CONNECTED) {

    Serial.println("\n========== WIFI INFORMATION ==========");

    Serial.println("WiFi Status: CONNECTED");

    Serial.print("WiFi Name: ");
    Serial.println(ssid);

    Serial.print("ESP32 IP Address: ");
    Serial.println(WiFi.localIP());

    Serial.print("Open Browser: http://");
    Serial.println(WiFi.localIP());

    Serial.print("WiFi Signal: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");

    Serial.println("======================================");

  } else {

    Serial.println("WiFi Status: DISCONNECTED");
  }
}

// ==================================================
// 14. SENSOR READING
// ==================================================

void readSensors() {

  soilValue = analogRead(SOIL_PIN);
  rainValue = analogRead(RAIN_PIN);
  waterValue = analogRead(WATER_PIN);

  // Soil moisture calculation

  moisture =
    (float)(DRY_VALUE - soilValue) /
    (DRY_VALUE - WET_VALUE) * 100.0;

  moisture = constrain(moisture, 0.0, 100.0);

  // DHT22 readings

  float t = dht.readTemperature();
  float h = dht.readHumidity();

  if (!isnan(t) && !isnan(h)) {

    temperature = t;
    humidity = h;
    dhtError = false;

  } else {

    dhtError = true;
  }

  // Water availability

  if (WATER_VALUE_HIGH_WHEN_WET) {

    waterAvailable = waterValue >= WATER_THRESHOLD;

  } else {

    waterAvailable = waterValue <= WATER_THRESHOLD;
  }

  // Rain detection

  if (RAIN_VALUE_LOW_WHEN_WET) {

    rainDetected = rainValue <= RAIN_THRESHOLD;

  } else {

    rainDetected = rainValue >= RAIN_THRESHOLD;
  }

  // Serial output

  Serial.println("\n========== SENSOR DATA ==========");

  Serial.print("Soil Moisture: ");
  Serial.print(moisture, 1);
  Serial.println("%");

  Serial.print("Soil Raw: ");
  Serial.println(soilValue);

  Serial.print("Rain Raw: ");
  Serial.println(rainValue);

  Serial.print("Rain Status: ");
  Serial.println(rainDetected ? "RAIN DETECTED" : "NO RAIN");

  Serial.print("Water Raw: ");
  Serial.println(waterValue);

  Serial.print("Water Status: ");
  Serial.println(waterAvailable ? "AVAILABLE" : "UNAVAILABLE");

  if (!dhtError) {

    Serial.print("Temperature: ");
    Serial.print(temperature, 1);
    Serial.println(" C");

    Serial.print("Humidity: ");
    Serial.print(humidity, 1);
    Serial.println("%");

  } else {

    Serial.println("DHT22 Sensor Error");
  }

  Serial.print("Pump Mode: ");
  Serial.println(getPumpMode());

  Serial.print("Pump Status: ");
  Serial.println(pumpOn ? "ON" : "OFF");

  Serial.println("---------------------------------");

  printWiFiInfo();

  Serial.println("=================================");
}

// ==================================================
// 15. PUMP CONTROL
// ==================================================

void controlPump() {

  unsigned long currentTime = millis();

  // Safety 1: water availability and rain protection

  if (!safetyConditionsOK()) {

    stopPump(irrigationStatus);
    return;
  }

  // Safety 2: maximum continuous pump runtime

  if (pumpOn &&
      currentTime - pumpStartedAt >= MAX_PUMP_RUNTIME) {

    stopPump("Pump stopped: maximum runtime reached");

    Serial.println("PUMP STOPPED: MAXIMUM RUNTIME");

    // Do not automatically repeat a manual ON request.
    if (pumpMode == MANUAL_ON_MODE) {
      pumpMode = MANUAL_OFF_MODE;
    }

    return;
  }

  // Manual OFF mode

  if (pumpMode == MANUAL_OFF_MODE) {

    stopPump("Manual mode: pump OFF");
    return;
  }

  // Manual ON mode

  if (pumpMode == MANUAL_ON_MODE) {

    if (!pumpOn) {

      if (!cooldownFinished()) {

        irrigationStatus = "Pump waiting for cooldown";
        return;
      }

      startPump();
    }

    return;
  }

  // Automatic mode

  if (moisture >= MOISTURE_THRESHOLD) {

    stopPump("Soil moisture sufficient");
    return;
  }

  if (!pumpOn) {

    if (!cooldownFinished()) {

      irrigationStatus = "Automatic mode: cooldown";
      return;
    }

    startPump();
  }
}

// ==================================================
// 16. JSON SENSOR API
// ==================================================

void handleSensorAPI() {

  String json = "{";

  json += "\"project\":\"AgriSense AI\",";
  json += "\"soil_raw\":" + String(soilValue) + ",";
  json += "\"soil_moisture\":" + String(moisture, 1) + ",";
  json += "\"rain_raw\":" + String(rainValue) + ",";
  json += "\"rain_detected\":";
  json += rainDetected ? "true" : "false";
  json += ",";

  json += "\"water_raw\":" + String(waterValue) + ",";
  json += "\"water_available\":";
  json += waterAvailable ? "true" : "false";
  json += ",";

  json += "\"temperature\":";

  if (dhtError) {
    json += "null";
  } else {
    json += String(temperature, 1);
  }

  json += ",";

  json += "\"humidity\":";

  if (dhtError) {
    json += "null";
  } else {
    json += String(humidity, 1);
  }

  json += ",";

  json += "\"dht_error\":";
  json += dhtError ? "true" : "false";
  json += ",";

  json += "\"pump_on\":";
  json += pumpOn ? "true" : "false";
  json += ",";

  json += "\"pump_mode\":\"" + getPumpMode() + "\",";
  json += "\"irrigation_status\":\"" + irrigationStatus + "\",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"rssi\":" + String(WiFi.RSSI());

  json += "}";

  server.send(200, "application/json", json);
}

// ==================================================
// 17. MANUAL AND AUTOMATIC PUMP API
// ==================================================

void handlePumpControl() {

  if (!server.hasArg("mode")) {

    server.send(
      400,
      "application/json",
      "{\"error\":\"Missing mode. Use on, off or auto.\"}"
    );

    return;
  }

  String requestedMode = server.arg("mode");
  requestedMode.toLowerCase();

  if (requestedMode == "on") {

    pumpMode = MANUAL_ON_MODE;

    controlPump();

  } else if (requestedMode == "off") {

    pumpMode = MANUAL_OFF_MODE;

    stopPump("Manual mode: pump OFF");

  } else if (requestedMode == "auto") {

    pumpMode = AUTO_MODE;

    controlPump();

  } else {

    server.send(
      400,
      "application/json",
      "{\"error\":\"Invalid mode. Use on, off or auto.\"}"
    );

    return;
  }

  String json = "{";

  json += "\"pump_on\":";
  json += pumpOn ? "true" : "false";
  json += ",";

  json += "\"pump_mode\":\"" + getPumpMode() + "\",";
  json += "\"irrigation_status\":\"" + irrigationStatus + "\"";

  json += "}";

  server.send(200, "application/json", json);
}

// ==================================================
// 18. EXISTING DASHBOARD
// ==================================================

void handleRoot() {

  String page = R"rawliteral(
<!DOCTYPE html>
<html>
<head>

<meta name="viewport" content="width=device-width, initial-scale=1">

<meta http-equiv="refresh" content="2">

<title>AgriSense AI</title>

<style>

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  padding: 14px;
  background: #edf6ee;
  font-family: Arial, sans-serif;
  color: #26352c;
}

.header {
  background: #176b3c;
  color: white;
  text-align: center;
  padding: 35px 15px;
  border-radius: 16px;
  margin-bottom: 24px;
}

.header h1 {
  font-size: 36px;
  margin: 15px 0;
}

.header h2 {
  font-size: 27px;
  margin: 22px 0;
}

.header p {
  font-size: 19px;
}

.dashboard {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 17px;
}

.card {
  background: white;
  padding: 24px;
  min-height: 175px;
  border-radius: 15px;
  box-shadow: 0 2px 7px rgba(0,0,0,0.04);
}

.card h3 {
  color: #176b3c;
  font-size: 21px;
  margin-bottom: 22px;
}

.value {
  font-size: 27px;
  font-weight: bold;
  margin-bottom: 15px;
  overflow-wrap: anywhere;
}

.detail {
  font-size: 18px;
  line-height: 1.5;
}

.green {
  color: #16803c;
}

.red {
  color: #d62828;
}

.footer {
  margin-top: 22px;
  font-size: 17px;
}

@media(max-width: 600px) {

  body {
    padding: 10px;
  }

  .header h1 {
    font-size: 27px;
  }

  .header h2 {
    font-size: 21px;
  }

  .card {
    min-height: 145px;
  }
}

</style>
</head>

<body>

<div class="header">

  <h1>Hi Harsha!</h1>

  <h2>AgriSense AI Smart Irrigation</h2>

  <p>ESP32 Live Dashboard</p>

</div>

<div class="dashboard">

  <div class="card">
    <h3>Soil Moisture</h3>
    <div class="value">%MOISTURE%%</div>
    <div class="detail">Raw: %SOIL%</div>
    <div class="detail">Condition: %CONDITION%</div>
  </div>

  <div class="card">
    <h3>Water Sensor</h3>
    <div class="value">%WATERSTATUS%</div>
    <div class="detail">Raw: %WATER%</div>
  </div>

  <div class="card">
    <h3>Rain Sensor</h3>
    <div class="value">%RAINSTATUS%</div>
    <div class="detail">Raw: %RAIN%</div>
  </div>

  <div class="card">
    <h3>Temperature</h3>
    <div class="value">%TEMP%</div>
  </div>

  <div class="card">
    <h3>Humidity</h3>
    <div class="value">%HUMIDITY%</div>
  </div>

  <div class="card">
    <h3>Pump Status</h3>
    <div class="value %PUMPCLASS%">%PUMPSTATUS%</div>
    <div class="detail">Mode: %PUMPMODE%</div>
  </div>

  <div class="card">
    <h3>Irrigation Status</h3>
    <div class="detail">%IRRIGATION%</div>
  </div>

  <div class="card">
    <h3>ESP32 IP Address</h3>
    <div class="detail">%IP%</div>
    <br>
    <div class="detail">WiFi signal: %RSSI% dBm</div>
  </div>

</div>

<div class="footer">
  Auto-refresh every 2 seconds |
  <span id="clock">Loading time...</span>
</div>

<script>

function updateClock() {

  document.getElementById("clock").innerHTML =
    new Date().toLocaleTimeString("en-IN", {
      hour12: false
    });
}

updateClock();

setInterval(updateClock, 1000);

</script>

</body>
</html>
)rawliteral";

  String condition;

  if (moisture < MOISTURE_THRESHOLD) {

    condition = "DRY";

  } else if (moisture > 65) {

    condition = "WET";

  } else {

    condition = "MODERATE";
  }

  page.replace("%MOISTURE%", String(moisture, 1));
  page.replace("%SOIL%", String(soilValue));
  page.replace("%CONDITION%", condition);

  page.replace("%WATER%", String(waterValue));

  page.replace(
    "%WATERSTATUS%",
    waterAvailable ? "Available" : "Unavailable"
  );

  page.replace(
    "%RAIN%",
    String(rainValue)
  );

  page.replace(
    "%RAINSTATUS%",
    rainDetected ? "Rain Detected" : "No Rain"
  );

  page.replace(
    "%TEMP%",
    dhtError ? "Sensor error" :
    String(temperature, 1) + " C"
  );

  page.replace(
    "%HUMIDITY%",
    dhtError ? "Sensor error" :
    String(humidity, 1) + " %"
  );

  page.replace(
    "%PUMPCLASS%",
    pumpOn ? "green" : "red"
  );

  page.replace(
    "%PUMPSTATUS%",
    pumpOn ? "ON" : "OFF"
  );

  page.replace("%PUMPMODE%", getPumpMode());

  page.replace("%IRRIGATION%", irrigationStatus);

  page.replace("%IP%", WiFi.localIP().toString());

  page.replace("%RSSI%", String(WiFi.RSSI()));

  server.send(200, "text/html", page);
}

// ==================================================
// 19. START WEB SERVER AND REGISTER ROUTES
// ==================================================

void startWebServer() {

  if (!serverStarted && WiFi.status() == WL_CONNECTED) {

    server.on("/", HTTP_GET, handleRoot);

    // Python backend sensor endpoint
    server.on("/api/data", HTTP_GET, handleSensorAPI);

    // Pump control endpoint
    server.on("/api/pump", HTTP_GET, handlePumpControl);

    server.begin();

    serverStarted = true;

    Serial.println("Web Server Started!");

    Serial.println("Dashboard: /");
    Serial.println("Sensor API: /api/data");
    Serial.println("Pump API: /api/pump?mode=on|off|auto");

    printWiFiInfo();
  }
}

// ==================================================
// 20. WIFI CONNECTION
// ==================================================

void connectWiFi() {

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  WiFi.begin(ssid, password);

  Serial.println("\nConnecting to home WiFi...");

  unsigned long startTime = millis();

  while (WiFi.status() != WL_CONNECTED &&
         millis() - startTime < 20000) {

    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {

    Serial.println("\nWiFi Connected!");

    printWiFiInfo();

    startWebServer();

  } else {

    Serial.println("\nWiFi Connection Failed!");
    Serial.println("Check WiFi name and password.");
  }
}

// ==================================================
// 21. SETUP
// ==================================================

void setup() {

  Serial.begin(115200);

  analogReadResolution(12);

  pinMode(RELAY_PIN, OUTPUT);

  // Ensure pump starts OFF
  digitalWrite(RELAY_PIN, RELAY_OFF);

  pumpOn = false;

  dht.begin();

  Serial.println("\n================================");
  Serial.println(" AGRISENSE AI SMART IRRIGATION");
  Serial.println("================================");

  connectWiFi();

  // India time zone
  configTime(19800, 0, "pool.ntp.org", "time.google.com");

  readSensors();

  sensorTimer = millis();

  lastPumpStopAt = millis();

  irrigationStatus = "System ready - automatic mode";

  Serial.println("System setup completed.");
}

// ==================================================
// 22. MAIN LOOP
// ==================================================

void loop() {

  // WiFi reconnection
  if (WiFi.status() != WL_CONNECTED) {

    Serial.println("WiFi disconnected. Reconnecting...");

    serverStarted = false;

    connectWiFi();
  }

  if (serverStarted) {
    server.handleClient();
  }

  unsigned long currentTime = millis();

  // Read sensors every 2.5 seconds
  if (currentTime - sensorTimer >= SENSOR_INTERVAL) {

    sensorTimer = currentTime;

    readSensors();
  }

  // Apply pump safety and operating logic
  controlPump();

  if (serverStarted) {
    server.handleClient();
  }
}
