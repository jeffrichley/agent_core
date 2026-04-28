"""Credential data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Credential:
    """A stored credential with all fields."""

    service: str
    username: str
    password: str
    url: str = ""
    notes: str = ""


@dataclass
class CredentialSummary:
    """A credential summary without the password."""

    service: str
    username: str
    url: str = ""
