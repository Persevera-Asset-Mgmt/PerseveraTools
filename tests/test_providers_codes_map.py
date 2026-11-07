"""sgs, sidra, fred e anbima (índices) exigem `codes_map` desde que get_codes() saiu."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import persevera_tools.data as data
from persevera_tools.data.providers.anbima import AnbimaProvider
from persevera_tools.data.providers.base import DataProvider
from persevera_tools.data.providers.sgs import SGSProvider
from persevera_tools.data.providers.sidra import SidraProvider


def test_get_codes_removido():
    assert not hasattr(data, "get_codes")


@pytest.mark.parametrize("codes_map", [None, {}])
def test_require_codes_map_sem_mapa(codes_map):
    with pytest.raises(ValueError, match="codes_map"):
        DataProvider._require_codes_map(codes_map, "sgs")


@pytest.mark.parametrize(
    "call",
    [
        lambda: SGSProvider().get_data("sgs"),
        lambda: SidraProvider().get_data("sidra"),
        lambda: AnbimaProvider().get_data("anbima_indices"),
    ],
)
def test_providers_sem_mapa_falham_antes_da_rede(call):
    with patch("requests.get") as get:
        with pytest.raises(ValueError, match="codes_map"):
            call()
        get.assert_not_called()


def test_sgs_usa_codes_map():
    resp = MagicMock(status_code=200, text="x")
    resp.json.return_value = [{"data": "01/01/2026", "valor": "10.5"}]
    with patch("persevera_tools.data.providers.sgs.requests.get", return_value=resp) as get:
        df = SGSProvider().get_data("sgs", codes_map={"432": "brl_selic_target"})
    assert "bcdata.sgs.432" in get.call_args.args[0]
    assert df["code"].unique().tolist() == ["brl_selic_target"]
    assert df["date"].iloc[0] == pd.Timestamp("2026-01-01")
