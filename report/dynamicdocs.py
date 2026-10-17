"""
DynamicDocs API Client for compiling LaTeX documents into PDFs.
Interacts with https://api.advicement.io/v1/templates/pub-tex-to-pdf-with-pdflatex-v1/compile.
"""
import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

COMPILE_URL = "https://api.advicement.io/v1/templates/pub-tex-to-pdf-with-pdflatex-v1/compile"
DEFAULT_POLL_INTERVAL = 2.0  # seconds
DEFAULT_MAX_TIMEOUT = 80     # seconds
REQUEST_TIMEOUT = 30         # seconds for compile and download
STATUS_TIMEOUT = 15          # seconds for status polling


class DynamicDocsError(Exception):
    """Base exception for DynamicDocs API errors."""
    pass


class DynamicDocsConfigError(DynamicDocsError):
    """Raised when the DynamicDocs API key is missing or misconfigured."""
    pass


class DynamicDocsTimeoutError(DynamicDocsError):
    """Raised when compilation polling exceeds max_timeout."""
    pass


class DynamicDocsCompileError(DynamicDocsError):
    """
    Raised when LaTeX compilation fails (statusCode >= 400).
    Carries the HTTP status code and the last ~3000 characters of the compile log.
    """
    def __init__(self, message: str, status_code: int = None, log_tail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.log_tail = log_tail

    def __str__(self):
        base = super().__str__()
        if self.log_tail:
            return f"{base}\n--- Log Tail ---\n{self.log_tail}"
        return base


def get_api_key(provided_key: str = None) -> str:
    """Retrieve the DynamicDocs API key from parameter or ADV_API_KEY environment variable."""
    key = (provided_key or os.environ.get("ADV_API_KEY", "")).strip()
    if not key:
        raise DynamicDocsConfigError(
            "DynamicDocs API key is not configured. Please set the ADV_API_KEY environment variable."
        )
    return key


def compile_pdf(
    payload_or_obj,
    api_key: str = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_timeout: int = DEFAULT_MAX_TIMEOUT,
    session: requests.Session = None,
) -> bytes:
    """
    Submit a LaTeX payload to DynamicDocs and poll until the PDF is ready or an error occurs.

    Parameters:
      payload_or_obj: Either an ieee object with .get_payload() or a dictionary with keys:
                      {"texFileContent": "...", "image1": "...", ...}
      api_key: Optional API key override. If not provided, reads ADV_API_KEY from environment.
      poll_interval: Seconds to wait between polling attempts (default: 2.0s).
      max_timeout: Maximum total seconds to poll before timing out (default: 80s).
      session: Optional requests.Session to reuse connections.

    Returns:
      bytes: The binary PDF content.

    Raises:
      DynamicDocsConfigError: If ADV_API_KEY is missing.
      DynamicDocsCompileError: If compilation fails (statusCode >= 400) with log tail.
      DynamicDocsTimeoutError: If compilation polling exceeds max_timeout.
      DynamicDocsError: For network or protocol errors.
    """
    token = get_api_key(api_key)

    # Extract dictionary payload
    if hasattr(payload_or_obj, "get_payload") and callable(payload_or_obj.get_payload):
        payload = payload_or_obj.get_payload()
    elif isinstance(payload_or_obj, dict):
        payload = payload_or_obj
    else:
        raise DynamicDocsError(f"Invalid payload type: expected dict or ieee object, got {type(payload_or_obj)}.")

    http = session or requests

    headers = {
        "Adv-Security-Token": token,
        "Content-Type": "application/json",
    }

    logger.info("Submitting LaTeX compilation job to DynamicDocs...")
    try:
        response = http.post(
            COMPILE_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as e:
        logger.error(f"Network error when connecting to DynamicDocs compile endpoint: {e}")
        raise DynamicDocsError(f"Network error submitting compile job: {e}") from e

    if response.status_code >= 400:
        error_msg = f"DynamicDocs submission rejected with HTTP {response.status_code}: {response.text}"
        logger.error(error_msg)
        raise DynamicDocsCompileError(
            message=error_msg,
            status_code=response.status_code,
            log_tail=response.text[:3000]
        )

    try:
        initial_data = response.json()
    except (ValueError, TypeError) as e:
        raise DynamicDocsError(f"Invalid JSON response from compile endpoint: {response.text}") from e

    document_status_url = initial_data.get("documentStatusUrl")
    if not document_status_url:
        raise DynamicDocsError(
            f"Response missing required 'documentStatusUrl'. Response was: {initial_data}"
        )

    # If the document is already immediately available
    if initial_data.get("documentUrl"):
        return _download_pdf(initial_data["documentUrl"], http)

    # Polling loop
    start_time = time.time()
    logger.info(f"Polling compilation status at {document_status_url} every {poll_interval}s...")

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_timeout:
            raise DynamicDocsTimeoutError(
                f"DynamicDocs compilation timed out after {elapsed:.1f}s (max timeout: {max_timeout}s)."
            )

        time.sleep(poll_interval)

        try:
            status_resp = http.get(document_status_url, timeout=STATUS_TIMEOUT)
        except requests.RequestException as e:
            logger.warning(f"Temporary network error while polling status URL: {e}")
            continue

        if status_resp.status_code != 200:
            logger.warning(
                f"Polling returned non-200 status {status_resp.status_code}: {status_resp.text}"
            )
            continue

        try:
            status_data = status_resp.json()
        except (ValueError, TypeError):
            logger.warning(f"Polling returned non-JSON response: {status_resp.text}")
            continue

        status_code = status_data.get("statusCode")

        # Check for compilation error (statusCode >= 400)
        if status_code is not None and int(status_code) >= 400:
            log_url = status_data.get("latexLogUrl") or status_data.get("calculationLogUrl")
            log_content = ""
            if log_url:
                try:
                    log_resp = http.get(log_url, timeout=STATUS_TIMEOUT)
                    if log_resp.status_code == 200:
                        log_content = log_resp.text
                except Exception as log_err:
                    logger.warning(f"Could not retrieve error log from {log_url}: {log_err}")

            log_tail = log_content[-3000:] if len(log_content) > 3000 else log_content
            error_msg = f"LaTeX compilation failed with statusCode {status_code}."
            logger.error(f"{error_msg}\nLog tail:\n{log_tail}")
            raise DynamicDocsCompileError(
                message=error_msg,
                status_code=int(status_code),
                log_tail=log_tail,
            )

        # Check for ready document
        document_url = status_data.get("documentUrl")
        if document_url:
            logger.info(f"Compilation succeeded in {elapsed:.1f}s. Downloading PDF...")
            return _download_pdf(document_url, http)


def _download_pdf(url: str, http) -> bytes:
    """Download the final compiled PDF from a signed documentUrl."""
    try:
        doc_resp = http.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise DynamicDocsError(f"Network error downloading compiled PDF: {e}") from e

    if doc_resp.status_code != 200:
        raise DynamicDocsError(
            f"Failed to download generated PDF from {url}: HTTP {doc_resp.status_code}"
        )

    return doc_resp.content
