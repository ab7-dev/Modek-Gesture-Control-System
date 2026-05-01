/*
  Modek — Arduino Uno R4 WiFi Sketch
  ─────────────────────────────────────
  Hosts an HTTP server on port 80.
  Receives POST /command from service2_analysis.py (WiFi mode).
  Controls LEDs and buzzer on breadboard.
  Displays the last command on the built-in 12x8 LED matrix.

  Required libraries (install via Arduino IDE Library Manager):
    - WiFiS3          (built-in for Uno R4 WiFi)
    - Arduino_LED_Matrix (built-in for Uno R4 WiFi)
    - ArduinoGraphics (install from Library Manager)

  Pin mapping (breadboard):
    Pin 2  — Green LED  — Light
    Pin 3  — Blue LED   — Fan
    Pin 4  — Red LED    — Alarm
    Pin 5  — Buzzer     — Alarm sound
    Pin 6  — Green LED  — AC
    Pin 7  — Blue LED   — TV

  Wiring:
    Each LED: Pin → 220Ω resistor → LED(+) → GND
    Buzzer:   Pin 5 → Buzzer(+) → GND

  Configuration:
    Set WIFI_SSID and WIFI_PASS below before uploading.
    After upload, open Serial Monitor (115200 baud) to see the IP address.
    Set that IP in your .env file as ARDUINO_HOST.
*/

// ArduinoGraphics must be included BEFORE Arduino_LED_Matrix
#include "ArduinoGraphics.h"
#include "Arduino_LED_Matrix.h"
#include "WiFiS3.h"

// ── WiFi credentials — set these before uploading ────────────────────────────
const char WIFI_SSID[] = "YOUR_HOTSPOT_SSID";
const char WIFI_PASS[] = "YOUR_HOTSPOT_PASSWORD";

// ── Pin definitions ───────────────────────────────────────────────────────────
#define PIN_LIGHT      2   // Green LED  — Light
#define PIN_FAN        3   // Blue LED   — Fan
#define PIN_ALARM_LED  4   // Red LED    — Alarm
#define PIN_BUZZER     5   // Buzzer     — Alarm sound
#define PIN_AC         6   // Green LED  — AC
#define PIN_TV         7   // Blue LED   — TV

// ── Globals ───────────────────────────────────────────────────────────────────
WiFiServer server(80);
ArduinoLEDMatrix matrix;
int wifiStatus = WL_IDLE_STATUS;

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    while (!Serial);

    // Initialise pins
    int pins[] = {PIN_LIGHT, PIN_FAN, PIN_ALARM_LED, PIN_BUZZER, PIN_AC, PIN_TV};
    for (int p : pins) {
        pinMode(p, OUTPUT);
        digitalWrite(p, LOW);
    }

    // Startup flash — confirms all LEDs are wired correctly
    for (int p : pins) digitalWrite(p, HIGH);
    delay(500);
    for (int p : pins) digitalWrite(p, LOW);

    // Initialise LED matrix
    matrix.begin();
    showMatrix("INIT");

    // Connect to WiFi
    if (WiFi.status() == WL_NO_MODULE) {
        Serial.println("ERROR: WiFi module not found");
        showMatrix("ERR");
        while (true);
    }

    Serial.print("Connecting to: ");
    Serial.println(WIFI_SSID);
    showMatrix("WIFI");

    int attempts = 0;
    while (wifiStatus != WL_CONNECTED && attempts < 10) {
        wifiStatus = WiFi.begin(WIFI_SSID, WIFI_PASS);
        Serial.print(".");
        delay(3000);
        attempts++;
    }

    if (wifiStatus != WL_CONNECTED) {
        Serial.println("\nERROR: Could not connect to WiFi");
        showMatrix("FAIL");
        while (true);
    }

    server.begin();

    Serial.println("\nConnected!");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
    Serial.print("Set ARDUINO_HOST=");
    Serial.println(WiFi.localIP());

    // Show IP on matrix briefly then show READY
    showMatrix("RDY");
}

// ── Main loop ─────────────────────────────────────────────────────────────────
void loop() {
    WiFiClient client = server.available();
    if (!client) return;

    Serial.println("Client connected");
    String requestLine = "";
    String body = "";
    bool isPost = false;
    bool headersDone = false;
    int contentLength = 0;

    // Read HTTP request
    while (client.connected()) {
        if (!client.available()) continue;

        String line = client.readStringUntil('\n');
        line.trim();

        if (!headersDone) {
            if (line.startsWith("POST")) isPost = true;
            if (line.startsWith("Content-Length:")) {
                contentLength = line.substring(16).toInt();
            }
            // Blank line = end of headers
            if (line.length() == 0) {
                headersDone = true;
                // Read body
                if (isPost && contentLength > 0) {
                    unsigned long timeout = millis() + 1000;
                    while (body.length() < (unsigned int)contentLength && millis() < timeout) {
                        if (client.available()) body += (char)client.read();
                    }
                }
                break;
            }
        }
    }

    // Handle /ping — used by Python WiFiClient to probe connectivity
    // Handle /command — main command endpoint
    String response = "";
    if (!isPost && requestLine.indexOf("/ping") >= 0) {
        response = "OK";
    } else if (isPost) {
        String cmd = extractCmd(body);
        if (cmd.length() > 0) {
            handleCommand(cmd);
            response = "ACK:" + cmd;
            Serial.println("CMD: " + cmd);
        } else {
            response = "ERR:PARSE";
        }
    } else {
        response = "ERR:METHOD";
    }

    // Send HTTP response
    client.println("HTTP/1.1 200 OK");
    client.println("Content-Type: text/plain");
    client.println("Connection: close");
    client.println();
    client.println(response);
    client.stop();
    Serial.println("Client disconnected");
}

// ── Command handler ───────────────────────────────────────────────────────────
void handleCommand(String cmd) {
    if (cmd == "LIGHT_ON") {
        digitalWrite(PIN_LIGHT, HIGH);
        showMatrix("L ON");

    } else if (cmd == "LIGHT_OFF") {
        digitalWrite(PIN_LIGHT, LOW);
        showMatrix("LOFF");

    } else if (cmd == "FAN_ON") {
        digitalWrite(PIN_FAN, HIGH);
        showMatrix("F ON");

    } else if (cmd == "FAN_OFF") {
        digitalWrite(PIN_FAN, LOW);
        showMatrix("FOFF");

    } else if (cmd == "AC_ON") {
        digitalWrite(PIN_AC, HIGH);
        showMatrix("A ON");

    } else if (cmd == "AC_OFF") {
        digitalWrite(PIN_AC, LOW);
        showMatrix("AOFF");

    } else if (cmd == "TV_ON") {
        digitalWrite(PIN_TV, HIGH);
        showMatrix("T ON");

    } else if (cmd == "TV_OFF") {
        digitalWrite(PIN_TV, LOW);
        showMatrix("TOFF");

    } else if (cmd == "ALARM") {
        showMatrix("ALRM");
        // 3-second alarm: red LED + buzzer beep pattern, then auto-off
        unsigned long start = millis();
        while (millis() - start < 3000) {
            digitalWrite(PIN_ALARM_LED, HIGH);
            digitalWrite(PIN_BUZZER,    HIGH); delay(200);
            digitalWrite(PIN_ALARM_LED, LOW);
            digitalWrite(PIN_BUZZER,    LOW);  delay(200);
        }
        digitalWrite(PIN_ALARM_LED, LOW);
        digitalWrite(PIN_BUZZER,    LOW);
        showMatrix("RDY");

    } else {
        Serial.println("ERR:UNKNOWN: " + cmd);
    }
}

// ── LED matrix helper ─────────────────────────────────────────────────────────
void showMatrix(const char* text) {
    matrix.beginDraw();
    matrix.stroke(0xFFFFFFFF);
    matrix.textScrollSpeed(80);
    matrix.textFont(Font_5x7);
    matrix.beginText(0, 1, 0xFFFFFF);
    matrix.println(text);
    matrix.endText(SCROLL_LEFT);
    matrix.endDraw();
}

// ── JSON body parser ──────────────────────────────────────────────────────────
// Extracts "cmd" value from {"cmd": "LIGHT_ON"} without a JSON library.
String extractCmd(String body) {
    int keyIdx = body.indexOf("\"cmd\"");
    if (keyIdx < 0) return "";
    int colonIdx = body.indexOf(":", keyIdx);
    if (colonIdx < 0) return "";
    int openQuote = body.indexOf("\"", colonIdx + 1);
    if (openQuote < 0) return "";
    int closeQuote = body.indexOf("\"", openQuote + 1);
    if (closeQuote < 0) return "";
    return body.substring(openQuote + 1, closeQuote);
}
