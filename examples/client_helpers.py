"""
Helper functions for examples
"""
import os
import time
import base64
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

# Load environment variables
load_dotenv()

console = Console()

# Settings
SCRAPER_URL = os.getenv("OPEN_SCRAPER_URL", "http://localhost:8000")
API_BASE = f"{SCRAPER_URL}/api/v1"


def submit_scrape_job(
    url: str,
    *,
    proxy_type: str = "none",
    proxy_pool_id: Optional[str] = None,
    proxy_geo: Optional[Dict] = None,
    device: str = "desktop",
    extract: Optional[Dict] = None,
    screenshot: bool = False,
    raw_html: bool = False,
    wait_for_selector: Optional[str] = None,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 30000,
    stealth: bool = True,
) -> str:
    """
    Submit a scraping job

    Returns:
        job_id: Job ID
    """
    payload = {
        "url": url,
        "proxy_type": proxy_type,
        "device": device,
        "screenshot": screenshot,
        "raw_html": raw_html,
        "timeout_ms": timeout_ms,
        "stealth": stealth,
        "wait_until": wait_until,
    }

    if proxy_pool_id:
        payload["proxy_pool_id"] = proxy_pool_id
    if proxy_geo:
        payload["proxy_geo"] = proxy_geo
    if extract:
        payload["extract"] = extract
    if wait_for_selector:
        payload["wait_for_selector"] = wait_for_selector

    with httpx.Client(timeout=60.0) as client:
        response = client.post(f"{API_BASE}/scrape/page", json=payload)
        response.raise_for_status()
        return response.json()["job_id"]


def submit_batch_job(pages: list) -> str:
    """
    Submit multiple pages as a single batch job.
    All pages run concurrently in the worker pool.

    Args:
        pages: List of page dicts, each with the same fields as submit_scrape_job

    Returns:
        job_id: Job ID
    """
    with httpx.Client(timeout=60.0) as client:
        response = client.post(f"{API_BASE}/scrape/pages", json={"pages": pages})
        response.raise_for_status()
        return response.json()["job_id"]


def batch_scrape_pages(pages: list) -> list:
    """
    Submit a batch of pages, wait for completion, return all results.

    Args:
        pages: List of page dicts

    Returns:
        results: List of scraping results (same order as input pages)
    """
    job_id = submit_batch_job(pages)
    wait_for_job(job_id)
    data = get_job_results(job_id)
    return data.get("results") or []


def wait_for_job(job_id: str, timeout: int = 120) -> Dict[str, Any]:
    """
    Wait for job completion

    Returns:
        job_status: Job status
    """
    deadline = time.time() + timeout

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scraping...", total=None)

        while time.time() < deadline:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(f"{API_BASE}/scrape/{job_id}")
                response.raise_for_status()
                status = response.json()

            if status["status"] == "done":
                progress.update(task, description="✓ Done!")
                return status
            elif status["status"] == "failed":
                progress.update(task, description="✗ Failed!")
                error_msg = status.get('error', 'Unknown error')
                raise RuntimeError(f"Job failed: {error_msg}")

            progress.update(
                task,
                description=f"Processing... ({status['done']}/{status['total']})",
            )
            time.sleep(1)

    raise TimeoutError(f"Job {job_id} did not complete in {timeout} seconds")


def get_job_results(job_id: str) -> Dict[str, Any]:
    """
    Get job results

    Returns:
        results: Scraping results
    """
    with httpx.Client(timeout=30.0) as client:
        response = client.get(f"{API_BASE}/scrape/{job_id}/results")
        response.raise_for_status()
        return response.json()


def scrape_page(
    url: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Scrape a single page (submit + wait + get results)

    Returns:
        result: Scraping result
    """
    console.print(f"[cyan]Scraping:[/cyan] {url}")

    job_id = submit_scrape_job(url, **kwargs)
    wait_for_job(job_id)
    results = get_job_results(job_id)

    items = results.get("results")
    if not items:
        raise RuntimeError(
            f"No results in response: status={results.get('status')}, error={results.get('error')}"
        )
    return items[0]


def save_screenshot(screenshot_base64: str, filename: str) -> Path:
    """
    Save screenshot from base64

    Returns:
        path: Path to saved file
    """
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    filepath = output_dir / filename

    screenshot_bytes = base64.b64decode(screenshot_base64)
    with open(filepath, "wb") as f:
        f.write(screenshot_bytes)

    console.print(f"[green]✓[/green] Screenshot saved: {filepath}")
    return filepath


def save_html(html: str, filename: str) -> Path:
    """
    Save HTML

    Returns:
        path: Path to saved file
    """
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    console.print(f"[green]✓[/green] HTML saved: {filepath}")
    return filepath


def print_result(result: Dict[str, Any]):
    """
    Pretty print result
    """
    meta = result["meta"]

    console.print("\n[bold cyan]Scraping Result:[/bold cyan]")
    console.print(f"  URL: {meta['url']}")
    console.print(f"  Status: {meta.get('status_code', 'N/A')}")
    console.print(f"  Device: {meta['device']}")
    console.print(f"  Proxy: {meta['proxy_type']}")
    console.print(f"  Retries: {meta['retries']}")
    console.print(f"  Time: {result['took_ms']}ms")

    if result.get("warnings"):
        console.print(f"  [yellow]⚠ Warnings:[/yellow] {len(result['warnings'])}")
        for warning in result["warnings"]:
            console.print(f"    - {warning}")

    if result.get("data"):
        console.print("\n[bold green]Extracted Data:[/bold green]")
        for key, value in result["data"].items():
            if isinstance(value, list):
                console.print(f"  {key}: [{len(value)} items]")
                for i, item in enumerate(value[:3]):  # Show first 3
                    console.print(f"    {i+1}. {str(item)[:80]}")
                if len(value) > 3:
                    console.print(f"    ... and {len(value)-3} more")
            else:
                val_str = str(value)[:100]
                console.print(f"  {key}: {val_str}")
