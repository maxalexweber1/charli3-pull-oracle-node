from fastapi.testclient import TestClient

from node.config.models import (
    AppConfig,
    ChainQueryConfig,
    CurrencyConfig,
    FeedConfig,
    NodeConfig,
    RateConfig,
    RewardCollectionConfig,
    UpdaterConfig,
)
from node.main import create_app


def test_create_app_with_mock_config():
    """
    Test that create_app successfully initializes the FastAPI application
    given a valid (mocked) AppConfig.
    """
    mock_rate_config = RateConfig(
        general_base_symbol="ADA-USD", base_currency=CurrencyConfig(exchanges=[])
    )

    mock_node_config = NodeConfig(
        mnemonic="test mnemonic",
        oracle_currency="test_currency",
        oracle_address="test_address",
        feeds=[
            FeedConfig(
                feed_id="default", asset_name="C3AS", rate=mock_rate_config,
            ),
        ],
    )

    mock_config = AppConfig(
        node=mock_node_config,
        updater=UpdaterConfig(),
        chain_query=ChainQueryConfig(network="mainnet"),
        reward_collection=RewardCollectionConfig(
            trigger_amount=100, reward_destination_address="test_dest"
        ),
    )

    app = create_app(mock_config)
    client = TestClient(app)

    # Check if we can hit the health endpoint
    # Note: In CI, this might return 503 if services aren't initialized,
    # but the app itself should be up and routing.
    response = client.get("/health")
    assert response.status_code in [200, 503]
    assert app.state.config == mock_config
