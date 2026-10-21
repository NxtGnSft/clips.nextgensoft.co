"""Shared copy for the configured upload size cap."""


def format_upload_limit(max_bytes: int) -> str:
    """Human size for the cap, using decimal GB to match the client."""
    if max_bytes >= 1_000_000_000:
        gigabytes = max_bytes / 1_000_000_000
        rounded = round(gigabytes, 1)
        if abs(rounded - round(rounded)) < 0.05:
            return f"{round(rounded):.0f} GB"
        return f"{rounded:.1f} GB"
    megabytes = max(1, round(max_bytes / 1_000_000))
    return f"{megabytes} MB"


def upload_too_large_detail(max_bytes: int) -> str:
    """Error text for a file over the cap. Keeps the historical 'too large' phrase."""
    return (
        "Uploaded file is too large. "
        f"The upload limit is {format_upload_limit(max_bytes)}."
    )
