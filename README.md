# print-engine

Template-based PDF generation microservice (Phase 5).

## Run locally

```bash
uvicorn app.main:app --port 9000
```

## Deploy (Railway)

- **Root directory**: `print-engine` (this folder)
- **Deploy method**: Dockerfile
- **Healthcheck**: `GET /health`
- **Port binding**: the container uses `PORT` (Railway) or `SERVICE_PORT` (fallback)

## Environment

Required:

- INTERNAL_API_KEY
- S3_BUCKET
- S3_REGION
- S3_ACCESS_KEY_ID
- S3_SECRET_ACCESS_KEY

Optional:

- S3_ENDPOINT (for S3-compatible providers)

Notes:

- `INTERNAL_API_KEY` must match what your backend uses when calling the print-engine (it is sent as `x-internal-key`).

## Test

PowerShell example:

```powershell
$body = @{
  job_id = 'job-123'
  svg_s3_key = 'path/to/template.svg'
  object_mm = @{ x = 0; y = 0; w = 146; h = 66 }
  series = @{ start = 'A0001'; count = 3; font = 'Helvetica'; font_size_mm = 4; x_ratio = 0.72; y_ratio = 0.61 }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9000/render" `
  -Headers @{ "x-internal-key" = "<KEY>" } `
  -ContentType "application/json" `
  -Body $body
```
