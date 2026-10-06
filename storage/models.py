from dataclasses import dataclass


@dataclass
class Email:
    subject: str
    body: str
    timestamp: int   # Unix seconds (internalDate // 1000)
    sender: str
    label: str
