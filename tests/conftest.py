"""Common fixtures for the Bouygues Telecom tests."""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.bouygues_telecom.const import (
    CONF_COOKIES,
    CONF_PERSON_ID,
    DOMAIN,
)

PERSON_ID = "800000000001"
MAIN_ID = "10000000001"
SECONDARY_ID = "10000000002"
USERNAME = "user@example.com"
PASSWORD = "secret"
COOKIES = [
    {
        "name": "MFATRUSTED",
        "value": "trusted",
        "domain": "mon-compte.bouyguestelecom.fr",
        "path": "/cas",
    },
    {
        "name": "CASTGC",
        "value": "tgc",
        "domain": "www.mon-compte.bouyguestelecom.fr",
        "path": "/cas",
    },
]


def make_id_token(person_id: str = PERSON_ID) -> str:
    """Return an unsigned JWT carrying the customer id."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"id_personne": person_id}).encode()
    ).decode()
    return f"header.{payload.rstrip('=')}.signature"


CONTRACTS: list[dict[str, Any]] = [
    {
        "id": MAIN_ID,
        "typeLigne": "MOBILE",
        "statut": "ACTIF",
        "numeroTel": "0600000001",
        "abonnement": {"libelle": "B&YOU 100Go 5G"},
        "personnalisationContrat": {"nom": "My phone"},
    },
    {
        "id": SECONDARY_ID,
        "typeLigne": "MOBILE",
        "statut": "ACTIF",
        "numeroTel": "0600000002",
        "abonnement": {"libelle": "Multi-SIM Internet"},
    },
]


def make_consumption(voice_total: int = -1) -> dict[str, Any]:
    """Return a suivi-conso-mobile payload."""
    return {
        "compteursConsoDATA": [
            {
                "quantiteConsommee": 0,
                "quantiteTotale": 37580963840.0,
                "type": "ADDITIONNELLE",
            },
            {
                "quantiteConsommee": 53687091200,
                "quantiteTotale": 107374182400,
                "type": "PRINCIPALE",
            },
        ],
        "compteursConsoVOIX": [
            {
                "quantiteConsommee": 120,
                "quantiteTotale": voice_total,
                "type": "PRINCIPALE",
            }
        ],
        "dateDebutConso": "2026-11-16",
        "dateReinitialisation": "2026-12-16",
        "dateReleve": "2026-11-26T23:48:19.522",
        "horsForfait": {"montantTotalHorsForfaitTTC": 1.5},
        "impactCarbone": {"equivalentGrammeCO2": 4841},
        "seuilDataHorsForfaitAtteint": True,
        "seuilDataHorsForfaitAtteintFREUR": True,
        "seuilDataHorsForfaitAtteintRDM": False,
    }


EXTRAS: dict[str, Any] = {
    "out_of_plan": {
        "sections": [{"libelle": "SMS+", "montant": 1.5, "_links": {"self": {}}}]
    },
    "top_ups": {"items": [{"libelle": "Recharge 5 Go", "_links": {"self": {}}}]},
    "incidents": {"incidentsCollectifs": [{"libelle": "Antenna down"}]},
}


def make_details(price: float, renewal: dict[str, Any] | None) -> dict[str, Any]:
    """Return the slow-changing data of a line."""
    return {
        "plan": {
            "offrePrincipale": {
                "libelleCommercial": "Forfait B&You 100 Go 5G",
                "tarifTTC": price,
                "tarifRemiseTTC": price / 2,
                "montantRemiseTTC": -price / 2,
            }
        },
        "renewal": {"items": [renewal] if renewal else []},
        "options": {
            "items": [
                {"libelle": "Multi-SIM Internet", "statut": "SOUSCRITE"},
                {"libelle": "Old option", "statut": "RESILIEE"},
            ]
        },
    }


DETAILS = {
    MAIN_ID: make_details(
        19.99,
        {
            "type": "renouvellement",
            "statut": True,
            "dateEligibilitePrevisionnelleRenouvellement": "2026-05-15",
        },
    ),
    SECONDARY_ID: make_details(0.0, {"type": "renouvellement", "statut": False}),
}

INVOICES: list[dict[str, Any]] = [
    {
        "idFacture": "1",
        "dateFacturation": "2026-11-15T23:00:00.000Z",
        "dateLimitePaieFacture": "2026-11-29T23:00:00.000Z",
        "mntTotFacture": 12.99,
        "soldeApresFacture": 12.99,
    }
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        unique_id=PERSON_ID,
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_PERSON_ID: PERSON_ID,
            CONF_COOKIES: COOKIES,
        },
    )


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch the API client used by the coordinator."""
    client = MagicMock()
    client.async_get_mobile_contracts = AsyncMock(
        side_effect=lambda: copy.deepcopy(CONTRACTS)
    )
    client.async_get_consumption = AsyncMock(
        side_effect=lambda cid: make_consumption(1800 if cid == SECONDARY_ID else -1)
    )
    client.async_get_contract_extras = AsyncMock(
        side_effect=lambda cid: copy.deepcopy(EXTRAS)
    )
    client.async_get_contract_details = AsyncMock(
        side_effect=lambda cid: copy.deepcopy(DETAILS[cid])
    )
    client.async_get_invoices = AsyncMock(side_effect=lambda: copy.deepcopy(INVOICES))
    with patch(
        "custom_components.bouygues_telecom.coordinator.BouyguesTelecomClient",
        return_value=client,
    ):
        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> MockConfigEntry:
    """Set up the integration."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
