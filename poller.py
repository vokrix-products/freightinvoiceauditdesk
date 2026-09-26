import os
import time
import json
import requests

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_SERVICE_KEY = os.environ['SUPABASE_SERVICE_KEY']
PRODUCT_ID = os.environ['PRODUCT_ID']
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

BUCKET = 'uploads'
RESULTS_BUCKET = 'results'

REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 3
POLL_INTERVAL_SECONDS = 60
STALE_CLAIM_MINUTES = 15

# PostgREST returns at most 1000 rows per select. Anything larger has to be
# paged, otherwise older rows are silently invisible to the caller.
HISTORY_PAGE_SIZE = 1000

# U.S. No 2 Diesel Retail Prices, weekly, dollars per gallon. The audit needs the
# price that applied when the shipment moved, so it takes the most recent weekly
# period on or before the invoice ship date.
EIA_SERIES_ID = 'PET.EMD_EPD2D_PTE_NUS_DPG.W'
EIA_URL = 'https://api.eia.gov/v2/seriesid/'

VALID_STATUSES = {
    'missing:critical',
    'expired:warning',
    'valid:good',
    'flagged:critical',
    'contract-review:warning',
    'unmapped:warning',
}

import processor
import rate_audit


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _request(method, url, retry=True, **kwargs):
    """HTTP call with a timeout on every attempt and bounded retries.

    retry=False for non-idempotent POSTs: if the request reached Supabase but the
    response was lost, replaying it would insert the record a second time.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    attempts = MAX_ATTEMPTS if retry else 1
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except Exception as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = 2 ** attempt
            print(f"request {method} {url} failed ({error}); retry in {delay}s", flush=True)
            time.sleep(delay)

    raise last_error


def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def download_file(bucket, file_path):
    if file_path.startswith(bucket + "/"):
        file_path = file_path[len(bucket) + 1:]
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_path}"
    resp = _request(
        "GET",
        url,
        headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "apikey": SUPABASE_SERVICE_KEY},
    )
    return resp.content


def fetch_pending_jobs():
    url = f"{SUPABASE_URL}/rest/v1/jobs"
    params = {
        "status": "eq.pending",
        "job_type": "eq.process_upload",
        "product_id": f"eq.{PRODUCT_ID}",
        "select": "*",
    }
    resp = _request("GET", url, headers=supabase_headers(), params=params)
    return resp.json()


def claim_job(job_id):
    """Atomically take ownership of a pending job so one poller processes it.

    Deliberately a single attempt with no retry. If the PATCH is applied but the
    response is lost, a replay would match 0 rows and we would wrongly conclude
    another worker owns the job. A failure here just leaves it pending for the
    next cycle.
    """
    headers = supabase_headers()
    headers["Prefer"] = "return=representation"
    url = f"{SUPABASE_URL}/rest/v1/jobs?id=eq.{job_id}&status=eq.pending"
    resp = requests.patch(
        url,
        headers=headers,
        json={"status": "processing", "started_at": _now_iso()},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    claimed = resp.json()
    return isinstance(claimed, list) and len(claimed) == 1


def release_stale_claims():
    """Return jobs abandoned mid-flight (poller died after claiming) to pending."""
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - STALE_CLAIM_MINUTES * 60),
    )
    headers = supabase_headers()
    headers["Prefer"] = "return=representation"
    resp = _request(
        "PATCH",
        f"{SUPABASE_URL}/rest/v1/jobs",
        headers=headers,
        params={
            "status": "eq.processing",
            "job_type": "eq.process_upload",
            "product_id": f"eq.{PRODUCT_ID}",
            "started_at": f"lt.{cutoff}",
        },
        json={"status": "pending"},
    )
    released = resp.json()
    if released:
        print(f"released {len(released)} stale claim(s)", flush=True)


def _paged_records(customer_id, select):
    """Every record row for one customer, oldest first.

    PostgREST caps a single select at 1000 rows, so a customer past that mark
    would otherwise have older rows silently invisible to the caller.
    """
    rows = []
    offset = 0

    while True:
        params = {
            "product_id": f"eq.{PRODUCT_ID}",
            "customer_id": f"eq.{customer_id}",
            "select": select,
            "limit": str(HISTORY_PAGE_SIZE),
            "offset": str(offset),
            "order": "id.asc",
        }
        resp = _request(
            "GET",
            f"{SUPABASE_URL}/rest/v1/records",
            headers=supabase_headers(),
            params=params,
        )
        page = resp.json()
        rows.extend(page)
        if len(page) < HISTORY_PAGE_SIZE:
            break
        offset += HISTORY_PAGE_SIZE

    return rows


def fetch_existing_invoice_numbers(customer_id, exclude_source_file_path=None):
    """Invoice numbers this customer has already had processed.

    Cross-upload duplicate detection needs history the processor cannot see: it
    only ever receives one file. Scoped to a single customer, because the same
    invoice number appearing under two different accounts is not a duplicate.

    Records written from the file currently being processed are skipped, so
    re-running a job does not flag its own previous output as a duplicate of
    itself.
    """
    numbers = set()

    for row in _paged_records(customer_id, "details,source_file_path"):
        if exclude_source_file_path and row.get("source_file_path") == exclude_source_file_path:
            continue
        details = row.get("details") or {}
        invoice_number = details.get("invoice_number")
        if invoice_number is not None and str(invoice_number).strip():
            numbers.add(str(invoice_number).strip())

    return numbers


def fetch_rate_lines(customer_id):
    """Contracted rate lines this customer has on file.

    Filtered on details.document_type after the fetch rather than with a
    PostgREST JSON path filter: a rejected JSON operator returns an empty set,
    which would silently make every invoice look like it had no contracted lane
    to audit against.
    """
    lines = []

    for row in _paged_records(customer_id, "title,details"):
        details = row.get("details") or {}
        if not isinstance(details, dict) or details.get("document_type") != "rate_sheet":
            continue
        line = dict(details)
        line.setdefault("title", row.get("title"))
        lines.append(line)

    return lines


_DIESEL_PRICE_CACHE = {}


def diesel_price_for(ship_date):
    """U.S. No 2 Diesel retail price for the week of the shipment.

    EIA publishes the series weekly, most recent first, with the period as the
    week-ending Monday. The audit takes the most recent period on or before the
    ship date.

    Returns None - never a guess - when the ship date is unknown, the key is
    unset, or the lookup fails. An unknown market price means the fuel surcharge
    cannot be audited, which the audit reports as contract-review rather than
    inventing a figure from the wrong week.
    """
    if ship_date is None:
        return None
    if ship_date in _DIESEL_PRICE_CACHE:
        return _DIESEL_PRICE_CACHE[ship_date]

    price = None
    api_key = (os.environ.get('EIA_API_KEY') or '').strip()

    if api_key:
        try:
            resp = _request(
                "GET",
                f"{EIA_URL}{EIA_SERIES_ID}",
                headers={},
                params={"api_key": api_key},
            )
            points = (resp.json().get("response") or {}).get("data") or []
            for point in points:
                period = point.get("period")
                value = point.get("value")
                if period is None or value is None:
                    continue
                if str(period) <= str(ship_date):
                    price = float(value)
                    break
        except Exception as error:
            print(f"EIA diesel lookup failed: {error}", flush=True)

    _DIESEL_PRICE_CACHE[ship_date] = price
    return price


def delete_prior_records(customer_id, file_path):
    """Remove records from an earlier run of the same file.

    Re-processing a job used to insert a second set of rows and leave the first
    ones behind, so every re-run doubled that file's records on the dashboard.

    Records with approved_at set are exempt: that column marks a record a user
    approved for TMS export, and silently deleting an approved record would
    destroy work. When any row for the file is approved, both sets are kept and
    the caller is told, rather than guessing which one the user meant.
    """
    base = {
        "product_id": f"eq.{PRODUCT_ID}",
        "customer_id": f"eq.{customer_id}",
        "source_file_path": f"eq.{file_path}",
    }

    approved = _request(
        "GET",
        f"{SUPABASE_URL}/rest/v1/records",
        headers=supabase_headers(),
        params={**base, "approved_at": "not.is.null", "select": "id", "limit": "1"},
    ).json()

    if approved:
        print(f"prior records for {file_path} are approved; keeping both sets", flush=True)
        return False

    _request(
        "DELETE",
        f"{SUPABASE_URL}/rest/v1/records",
        headers=supabase_headers(),
        params=base,
    )
    return True


def upload_results(job_id, results, customer_id=None):
    """Upload the result file into the owning customer's folder.

    The key layout matters. The dashboard downloads result files as the signed-in
    user with the anon key, and the `results` bucket policy only grants access
    when the first path segment equals their uid:

        (storage.foldername(name))[1] = auth.uid()

    A flat `<job_id>.json` key has no folder segment at all, so
    storage.foldername() returns an empty array, the comparison is NULL, and the
    policy denies every download. Nesting under `<customer_id>/` is what makes the
    existing policy match.
    """
    prefix = f"{customer_id}/" if customer_id else ""
    path = f"{prefix}{job_id}.json"
    url = f"{SUPABASE_URL}/storage/v1/object/{RESULTS_BUCKET}/{path}"
    resp = _request(
        "POST",
        url,
        data=json.dumps(results),
        headers={
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "apikey": SUPABASE_SERVICE_KEY,
            "Content-Type": "application/json",
            "x-upsert": "true",
        },
    )
    return f"{RESULTS_BUCKET}/{path}"


def update_job(job_id, status, output_file_path=None, result_summary=None, error_message=None):
    url = f"{SUPABASE_URL}/rest/v1/jobs?id=eq.{job_id}"
    payload = {"status": status, "completed_at": _now_iso()}
    if output_file_path is not None:
        payload["output_file_path"] = output_file_path
    if result_summary is not None:
        payload["result_summary"] = result_summary
    if error_message is not None:
        payload["error_message"] = error_message
    resp = _request("PATCH", url, headers=supabase_headers(), json=payload)


def notify(customer_id, success):
    try:
        payload = {
            "product_id": PRODUCT_ID,
            "customer_id": customer_id,
            "title": "Processing complete" if success else "Processing failed",
            "body": "Your upload has been processed successfully." if success else "There was an error processing your upload.",
            "type": "success" if success else "error",
            "read": False,
        }
        _request(
            "POST",
            f"{SUPABASE_URL}/rest/v1/notifications",
            headers=supabase_headers(),
            json=payload,
            retry=False,
        )
    except Exception as e:
        print(f"notification failed: {e}", flush=True)


def process_job(job):
    job_id = job["id"]
    customer_id = job["customer_id"]
    file_path = job["input_file_path"]

    try:
        claimed = claim_job(job_id)
    except Exception as error:
        print(f"job {job_id}: claim failed, leaving pending: {error}", flush=True)
        return

    if not claimed:
        return

    try:
        file_bytes = download_file(BUCKET, file_path)
        results = processor.process_file(file_bytes)
        if not isinstance(results, list):
            raise ValueError("processor.process_file must return a list")

        # Duplicate history is a nice-to-have, not a reason to fail an upload: if
        # the lookup is unavailable we process normally rather than losing the job.
        try:
            existing_invoices = fetch_existing_invoice_numbers(
                customer_id, exclude_source_file_path=file_path
            )
        except Exception as error:
            print(f"job {job_id}: duplicate lookup failed, continuing without it: {error}", flush=True)
            existing_invoices = set()
        results = processor.apply_cross_upload_duplicates(results, existing_invoices)

        # Compare each invoice against the customer's contracted rate lines. Runs
        # after duplicate detection so a flagged duplicate is never downgraded to
        # a contract-review finding, and before the insert so the stored record
        # carries the audit in one write.
        try:
            rate_lines = fetch_rate_lines(customer_id)
        except Exception as error:
            print(f"job {job_id}: rate line lookup failed, continuing without audit: {error}", flush=True)
            rate_lines = []
        if rate_lines:
            try:
                results = rate_audit.apply_rate_audit(results, rate_lines, diesel_price_for)
            except Exception as error:
                print(f"job {job_id}: rate audit failed, continuing: {error}", flush=True)

        # Replace the previous run of this same file, so re-processing a job does
        # not leave two sets of records on the dashboard. Skipped when a record
        # from the earlier run was approved by a user.
        try:
            delete_prior_records(customer_id, file_path)
        except Exception as error:
            print(f"job {job_id}: could not clear prior records, continuing: {error}", flush=True)

        inserted = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid record status: {status}")
            title = item.get("title")
            if not title or not str(title).strip():
                raise ValueError("Missing record title")
            record = {
                "product_id": PRODUCT_ID,
                "customer_id": customer_id,
                "title": title,
                "status": status,
                "details": item.get("details", {}),
                "source_file_path": file_path,
                "due_date": item.get("due_date"),
            }
            _request(
                "POST",
                f"{SUPABASE_URL}/rest/v1/records",
                headers=supabase_headers(),
                json=record,
                retry=False,
            )
            inserted += 1
        result_summary = f"Processed {inserted} records"
        output_file_path = upload_results(job_id, results, customer_id)
        update_job(job_id, "completed", output_file_path=output_file_path, result_summary=result_summary)
        notify(customer_id, True)
    except Exception as e:
        print(f"job {job_id} failed: {e}", flush=True)
        try:
            update_job(job_id, "failed", result_summary=str(e), error_message=str(e))
        except Exception as update_error:
            print(f"failed to update job: {update_error}", flush=True)
        try:
            notify(customer_id, False)
        except Exception as notify_error:
            print(f"failed to notify: {notify_error}", flush=True)


def poll():
    while True:
        try:
            release_stale_claims()
            jobs = fetch_pending_jobs()
            for job in jobs:
                process_job(job)
            print(f"poll ok: {len(jobs)} pending job(s) seen", flush=True)
        except Exception as e:
            print(f"polling error: {e}", flush=True)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    print(
        f"Poller started (product={PRODUCT_ID}, timeout={REQUEST_TIMEOUT}s, "
        f"retries={MAX_ATTEMPTS}, poll={POLL_INTERVAL_SECONDS}s)",
        flush=True,
    )
    poll()
