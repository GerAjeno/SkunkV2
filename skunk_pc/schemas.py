from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Printer:
    name: str
    uri: str
    description: str
    make_model: str
    state: str
    state_message: str
    connected: bool
    is_zebra: bool
    language: str
    dpi: int
    page_size: str
    media_type: str = "direct"
    physical_uri: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class UsbPrinter:
    uri: str
    manufacturer: str
    model: str
    serial: str
    is_zebra: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class NetworkPrinter:
    uri: str
    name: str
    model: str

    def as_dict(self) -> dict:
        return asdict(self)
