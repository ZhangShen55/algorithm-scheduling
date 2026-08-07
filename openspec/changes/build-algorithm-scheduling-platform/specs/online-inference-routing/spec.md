## ADDED Requirements

### Requirement: Online Base64 image boundary
`online-gateway-service` SHALL expose `/api/online/vbas/analyze`, `/api/online/face/recognize`, and `/api/online/image-quality/detect`. These APIs SHALL accept images supplied by the upstream caller and SHALL NOT ingest RTSP, pull video streams, or extract frames.

#### Scenario: Submit one online student image
- **WHEN** A sends one Base64 image to the online VBas API
- **THEN** the gateway selects one ready VBas instance, proxies the request, and returns its synchronous result

### Requirement: Request-level routing
The gateway SHALL route each complete HTTP request to exactly one operator instance. It SHALL NOT split a multi-image request across instances, although separate concurrent requests may route to different instances.

#### Scenario: Multi-image compatibility request
- **WHEN** a caller submits multiple images in one accepted request
- **THEN** the complete request is sent to one instance and item-level successes and failures are preserved

### Requirement: Online capacity isolation
Online routing SHALL acquire and release operator capacity leases without Kafka. Lack of ready capacity SHALL produce a bounded synchronous business response and SHALL NOT create an offline course node.

#### Scenario: All face instances are busy
- **WHEN** an online face recognition request arrives with no available face lease
- **THEN** the gateway returns a capacity-unavailable result without publishing Kafka work

### Requirement: Realtime ASR sticky sessions
The gateway SHALL select one `asr_online` instance when a WebSocket session is established and SHALL retain that binding until the session closes. Realtime transcription SHALL serve live subtitles and SHALL not replace or persist the formal offline ASR result by default.

#### Scenario: Realtime session produces subtitles
- **WHEN** a live player opens a WebSocket and streams audio
- **THEN** all frames for the session are proxied to the same online ASR instance and results are returned to the player without entering the offline DAG

