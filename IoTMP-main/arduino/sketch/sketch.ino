/*
  Modek — Arduino Demo Sketch
  ────────────────────────────
  Receives serial commands from service2_analysis.py and controls
  LEDs and buzzer on a breadboard to demonstrate smart home gestures.

  Pin mapping:
    Pin 2  — Green LED  — Light
    Pin 3  — Blue LED   — Fan
    Pin 4  — Red LED    — Alarm
    Pin 5  — Buzzer     — Alarm sound
    Pin 6  — Green LED  — AC
    Pin 7  — Blue LED   — TV
    Pin 13 — Built-in   — Heartbeat (blinks every second)

  Wiring:
    Each LED: Pin → 220Ω resistor → LED(+) → GND
    Buzzer:   Pin 5 → Buzzer(+) → GND

  Commands received over Serial (9600 baud):
    LIGHT_ON   LIGHT_OFF
    FAN_ON     FAN_OFF
    ALARM
    AC_ON      AC_OFF
    TV_ON      TV_OFF
*/

#define PIN_LIGHT      2
#define PIN_FAN        3
#define PIN_ALARM_LED  4
#define PIN_BUZZER     5
#define PIN_AC         6
#define PIN_TV         7
#define PIN_STATUS    13

void setup() {
    Serial.begin(9600);

    int pins[] = {PIN_LIGHT, PIN_FAN, PIN_ALARM_LED,
                  PIN_BUZZER, PIN_AC, PIN_TV, PIN_STATUS};
    for (int p : pins) {
        pinMode(p, OUTPUT);
        digitalWrite(p, LOW);
    }

    // Startup flash — confirms all LEDs are wired correctly.
    for (int p : pins) digitalWrite(p, HIGH);
    delay(500);
    for (int p : pins) digitalWrite(p, LOW);
    delay(200);

    Serial.println("READY");
}

void loop() {
    // Heartbeat: built-in LED blinks every second so you know Arduino is alive.
    digitalWrite(PIN_STATUS, (millis() / 1000) % 2);

    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        handleCommand(cmd);
    }
}

void handleCommand(String cmd) {
    if (cmd == "LIGHT_ON") {
        digitalWrite(PIN_LIGHT, HIGH);
        Serial.println("ACK:LIGHT_ON");

    } else if (cmd == "LIGHT_OFF") {
        digitalWrite(PIN_LIGHT, LOW);
        Serial.println("ACK:LIGHT_OFF");

    } else if (cmd == "FAN_ON") {
        digitalWrite(PIN_FAN, HIGH);
        Serial.println("ACK:FAN_ON");

    } else if (cmd == "FAN_OFF") {
        digitalWrite(PIN_FAN, LOW);
        Serial.println("ACK:FAN_OFF");

    } else if (cmd == "AC_ON") {
        digitalWrite(PIN_AC, HIGH);
        Serial.println("ACK:AC_ON");

    } else if (cmd == "AC_OFF") {
        digitalWrite(PIN_AC, LOW);
        Serial.println("ACK:AC_OFF");

    } else if (cmd == "TV_ON") {
        digitalWrite(PIN_TV, HIGH);
        Serial.println("ACK:TV_ON");

    } else if (cmd == "TV_OFF") {
        digitalWrite(PIN_TV, LOW);
        Serial.println("ACK:TV_OFF");

    } else if (cmd == "ALARM") {
        // 3-second alarm: red LED + buzzer beep pattern, then auto-off.
        unsigned long start = millis();
        while (millis() - start < 3000) {
            digitalWrite(PIN_ALARM_LED, HIGH);
            digitalWrite(PIN_BUZZER,    HIGH); delay(200);
            digitalWrite(PIN_ALARM_LED, LOW);
            digitalWrite(PIN_BUZZER,    LOW);  delay(200);
        }
        digitalWrite(PIN_ALARM_LED, LOW);
        digitalWrite(PIN_BUZZER,    LOW);
        Serial.println("ACK:ALARM");

    } else {
        Serial.print("ERR:UNKNOWN:");
        Serial.println(cmd);
    }
}
