🖐️ Modek — Finger Flick Gesture Control System

Modek is a real-time, vision-based human–computer interaction (HCI) system that performs kinematic gesture inference by mapping finger motion primitives to discrete smart-home control commands.

📑 Table of Contents
Overview
System Architecture
Gesture Processing Pipeline
Interaction Model
Hardware Integration
Calibration & Adaptation
Core Features
Tech Stack
📌 Overview

Modek leverages monocular RGB input, probabilistic hand landmark estimation, and signal processing techniques to enable low-latency, noise-resilient gesture recognition. It is designed for real-time performance and robust interaction in unconstrained environments.

🏗️ System Architecture

The system follows a distributed, event-driven dual-service architecture:

Service 1 (Capture Layer)
Performs high-frequency frame acquisition and MediaPipe-based 3D hand landmark regression. Streams normalized landmark data (~30 FPS) via WebSockets.
Service 2 (Analysis Layer)
Executes gesture inference using temporal modeling, signal conditioning, and decision logic.

This separation ensures scalability, modularity, and cross-device deployment.

⚙️ Gesture Processing Pipeline

The inference pipeline consists of multiple stages:

1. Landmark Acquisition
21-point hand landmark extraction using MediaPipe
Normalization to image-space coordinates
2. Signal Conditioning
Adaptive low-pass filtering via One Euro Filter
Dynamic cutoff tuning based on instantaneous velocity
Noise suppression while preserving rapid motion
3. Temporal Modeling
Per-finger abstraction using FingerUnit FSM
States: IDLE → RISING → DWELL
Captures motion phases and gesture lifecycle
4. Gesture Isolation
Winner-Takes-All (WTA) competitive selection
Isolation threshold to suppress inter-finger crosstalk
Ensures deterministic gesture attribution
5. Gesture Inference
Velocity peak detection with hysteresis
Dwell-time validation and spatial tolerance checks
Rejects jitter and tracking artifacts
🧠 Interaction Model

Modek implements a hierarchical gesture mapping system:

Normal Layer
Index → Light
Middle → Fan
Ring/Pinky → Alarm
Advanced Layer (Pinch Activated)
Middle → AC
Ring/Pinky → TV

A thumb–index pinch gesture acts as a state transition trigger between layers, enabling context-aware interaction.

🔌 Hardware Integration

The system includes a hardware abstraction layer supporting:

Serial (UART) communication with Arduino
HTTP-based communication (WiFi / ESP32)
Null fallback mode for software-only operation

This ensures fault tolerance and seamless operation across different hardware configurations.

🎯 Calibration & Adaptation

Modek incorporates an online calibration pipeline:

Computes user-specific displacement ranges
Derives velocity thresholds dynamically
Establishes stability tolerances for dwell detection

This enables adaptive normalization, improving robustness across:

Different users
Hand sizes
Camera distances
Environmental noise conditions
🚀 Core Features
Real-time 3D hand landmark tracking (MediaPipe)
FSM-based temporal gesture segmentation
Velocity peak detection with dwell-time hysteresis
Winner-Takes-All (WTA) signal isolation
Adaptive filtering (One Euro Filter)
Low-latency WebSocket streaming architecture
Pinch-based hierarchical interaction model
Arduino integration (UART / WiFi / fallback)
Dynamic, user-specific calibration
🛠️ Tech Stack
Language: Python
Computer Vision: OpenCV, MediaPipe
UI Framework: PyQt6
Networking: WebSockets
Numerical Computing: NumPy
Hardware: Arduino
