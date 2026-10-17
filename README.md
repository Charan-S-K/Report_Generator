# Report Generator — Django Backend

A Django backend for the IEEE conference-style PDF report generator. It manages document sections, references, metadata, and user-uploaded images via sessions and database storage, formats sanitized LaTeX, and compiles production-ready IEEEtran PDFs via the DynamicDocs API.

---

## 1. Prerequisites & Environment Setup

The backend runs on Python 3.12+ with Django in a local virtual environment (`.venv`).

### Setting the DynamicDocs API Key
DynamicDocs requires an API token to compile LaTeX into PDFs.
Create or update the `.env` file in the project root:

```bash
ADV_API_KEY="your-advicement-api-key-here"
```

The application automatically loads `.env` on startup using `python-dotenv`.

---

## 2. Running Database Migrations

Ensure all required tables (including Django sessions and `UploadedImage`) are created:

```bash
.venv/bin/python manage.py migrate
```

---

## 3. Running the Development Server

Start the Django local development server:

```bash
.venv/bin/python manage.py runserver
```

Once running, navigate to:
- Main SPA: `http://127.0.0.1:8000/`
- Health check: `http://127.0.0.1:8000/health/`
- Generation endpoint: `http://127.0.0.1:8000/generate/`

---

## 4. Running the Test Suite

The comprehensive test suite covers all components (Setup, References, Title/Authors, Abstract/Keywords, Sections/Elements, Images & Magic Bytes, LaTeX Builder, DynamicDocs Client, and Generation Rate Limiting):

```bash
.venv/bin/python manage.py test report
```

All external HTTP calls to DynamicDocs are fully mocked in unit tests.

---

## 5. Scheduling Image Purge (`purge_expired_images`)

Uploaded images expire automatically after 7 days (or 7 days from the last successful PDF generation). To periodically clean up expired database rows and remove their physical files from disk, run:

```bash
.venv/bin/python manage.py purge_expired_images
```

### Setting up a Daily Cron Job
To run this automatically once every night at 03:00 AM, add the following line to your system crontab (`crontab -e`):

```cron
0 3 * * * /Users/charantejsk/Desktop/Report_Generator/.venv/bin/python /Users/charantejsk/Desktop/Report_Generator/manage.py purge_expired_images >> /var/log/purge_expired_images.log 2>&1
```

---

## 6. API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health/` | Health check endpoint returning `{status: "ok", session_key: "..."}`. |
| `GET`, `POST` | `/references/` | Get or update references list in session (`ref1`, `ref2`, ...). |
| `GET`, `POST` | `/title-authors/` | Get or update document title and authors list in session. |
| `GET`, `POST` | `/abstract-keywords/` | Get or update abstract and keywords in session. |
| `GET`, `POST` | `/sections/` | Get or update structured sections and ordered elements in session. |
| `POST` | `/images/upload/` | Multipart upload for PNG/JPEG images (magic bytes validated, max 10MB). Returns image ID. |
| `GET` | `/generate/` | Serves the frontend single-page application. |
| `POST` | `/generate/` | Compiles session data into IEEE PDF via DynamicDocs and returns `application/pdf`. Rate-limited (1 req/10s, 10 req/hr). |

---

## 7. Architecture & Security Notes

- **Plain JSON in Sessions**: `request.session` stores only primitive JSON types (strings, lists, dicts) and image IDs. Never stores Python objects or base64 payloads.
- **Single-Pass LaTeX Escaping**: Special characters (`& % $ # _ { } ~ ^ \`) are escaped once at build time (`builder.py`).
- **Untrusted Equation Validation**: Equations are verified for balanced curly braces and screened against dangerous TeX commands (`\input`, `\def`, `\write`, etc.).
- **Session Isolation**: Image files on disk are verified against `request.session.session_key` to prevent cross-session access.

---

## 8. Download Flow

The frontend single-page application orchestrates an end-to-end save-and-download workflow when the user clicks **Generate PDF Document**:

1. **Client-Side Pre-Validation**:
   The frontend scans all section elements for any empty image blocks (blocks with neither uploaded `imageId` nor `previewData`). If found, generation halts immediately and prompts: `"Add an image or remove the empty image block in Section X."`

2. **Image Upload (`POST /images/upload/`)**:
   For any new image element containing preview data without an assigned `imageId`, the frontend converts the base64 data URL into a binary `Blob` and POSTs multipart `FormData` (`image`, `fmt`) with `X-CSRFToken`. The server validates magic bytes and size, saves the image to `media/report_images/`, and returns the created `image_id`, which the frontend attaches to the element. Changing or removing an image resets `imageId` to `null`.

3. **Form State Persistence**:
   The frontend sequentially saves all report parts to the session using JSON POST requests:
   - `POST /references/`: `{ references: [{ title, link }] }`
   - `POST /title-authors/`: `{ title, authors: [{ name, affiliation }] }`
   - `POST /abstract-keywords/`: `{ abstract, keywords }`
   - `POST /sections/`: Clean sections where image elements are sent strictly as `{ type: "image", caption, imageType, image_id }`. Base64 preview data is never stored in session or sent in section payloads.

4. **Document Compilation (`POST /generate/`)**:
   - The backend validates all session data (non-empty title, valid sections, existent images, valid equations).
   - Generates sanitized LaTeX following the IEEEtran conference format, single-pass escaping reserved characters (`%`, `&`, etc.).
   - Dispatches the payload to DynamicDocs API to compile the PDF.
   - On success, returns `application/pdf` with `Content-Disposition: attachment; filename="<safe-title>.pdf"` (sanitized, alphanumeric and dashes, max 60 chars, fallback `"report.pdf"`).

5. **Download Trigger & Error Recovery**:
   - **200 OK**: Frontend reads the binary blob, extracts the sanitized filename from the `Content-Disposition` header, triggers download via a temporary anchor element and `URL.createObjectURL`, and revokes the URL.
   - **400 Validation Error**: Shows the list of validation errors inside an error container in the Generate view.
   - **429 Rate Limit**: Displays cooldown warning and keeps the generate button disabled with an active countdown timer (`Wait 10s...`).
   - **502 Compile Error**: Displays compilation error summary with a collapsible view of the LaTeX error log tail.
   - **504 Timeout**: Displays compilation timeout guidance.

