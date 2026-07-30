# Workspace Reference Guide

This document provides links and brief descriptions of the various projects within this workspace. These projects serve as references and components for the main development of **EnhancedOpenPilot (EOP)**.

## Core Projects

### [openpilot](../README.md)
The main project: EnhancedOpenPilot (EOP), optimized for **Rockchip RK3588 (LubanCat5 BTB)** hardware. It features a stereo vision pipeline, SceneSeg, OccupancyGrid, and optional PCIe acceleration.

### enhancedopenpilot
A specialized fork of openpilot designed for RK3588-based hardware and the BrownPanda Chinese EV framework.

---

## Autonomous Driving Frameworks & Projects

### [autoware_universe](../../autoware_universe/README.md)
A foundational pillar of the Autoware ecosystem, managing packages that extend the capabilities of autonomous vehicles.

### [autoware_vision_pilot](../../autoware_vision_pilot/README.md)
Advanced Driver Assistance Systems (ADAS) and self-driving vision pipeline implementation.

### [visionpilot](../../visionpilot/README.md)
**Enhanced Vision Pilot (EVP)**: A lightweight, vision-first adaptation of ROS 2 Autoware paradigms for embedded Rockchip hardware (RK3588/RK3576).

### [dragonpilot](../../dragonpilot/README.md)
A fork of openpilot with additional features, customizations, and Chinese language support.

### [sunnypilot](../../sunnypilot/README.md)
A fork of openpilot offering a unique driving experience for over 300+ car models with modified assist behaviors.

### [FrogPilot](../../FrogPilot/README.md)
An operating system for robotics that upgrades the driver assistance system in many supported cars.

---

## Hardware & AI Acceleration (Rockchip/RK3588)

### [rknn-toolkit2](../../rknn-toolkit2/README.md)
Software development kit for model conversion, inference, and performance evaluation on Rockchip NPU platforms.

### [rknn_model_zoo](../../rknn_model_zoo/README.md)
A collection of deployment examples for mainstream algorithms using the RKNPU SDK toolchain.

### [librga](../../librga/README.md)
Rockchip Raster Graphic Acceleration (RGA) unit user-space driver and API for 2D graphics operations.

### [lubancat_ai_manual_code](../../lubancat_ai_manual_code/README.md)
Practical guide and code for embedded AI application development on LubanCat-RK series boards.

### [lubancat_rk_code_storage](../../lubancat_rk_code_storage/README.md)
Comprehensive repository for LubanCat-RK series tutorials covering Linux, drivers, Qt, and Python.

---

## Specialized Utilities & Tutorials

### ros2_socketcan
ROS 2 package for SocketCAN support.

### tc275_openblt
OpenBLT-based bootloader implementation for the Infineon TC275 TriCore microcontroller, designed for automotive ECU applications.

### tc275_freertos
FreeRTOS implementation for the Infineon TC275 TriCore microcontroller.

### [embed_qt_develop_tutorial_code](../../embed_qt_develop_tutorial_code/README.md)
Tutorial code for embedded Qt application development.

---

## Development Guildlines

Refer to CONVENTIONS.md for project-specific coding standards and architectural rules.
