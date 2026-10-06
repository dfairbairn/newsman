from dataclasses import dataclass


@dataclass
class Email:
    subject: str
    body: str
    timestamp: int   # Unix seconds (internalDate // 1000)
    sender: str
    label: str
    message_id: str = ''        # RFC822 Message-ID header; stable key for dedup
    injection_flags: str = ''   # comma-separated prompt-injection indicators found during sanitize
