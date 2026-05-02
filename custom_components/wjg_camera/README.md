# WJG XM-3820 Camera Bridge

Home Assistant Custom Integration for WJG/Tenganda XM-3820 based cameras.

## Features

- Camera entity with RTSP stream support
- Snapshot support with HTTP fallback
- Recording start/stop switch entity
- Motion binary sensor
- PTZ button entities
- File list sensor
- Optional ONVIF and XM SDK pathways

## Installation (HACS)

Repository:
https://github.com/beastwareteam/wjg-camera-ha

1. Add this repository as a custom repository in HACS.
2. Select category: Integration.
3. Install or update the integration.
4. Restart Home Assistant.
5. Add integration: WJG Camera.

## Notes

If HACS reports a generic error during update, remove the existing installation in HACS, restart Home Assistant, then install again.
