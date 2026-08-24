from trading_agent.config import load_config


def test_aggressive_profile_is_demo_only_and_keeps_hard_guards() -> None:
    config = load_config()
    rules = config.rules

    assert config.environment == "demo"
    assert rules["risk"] == {
        "risk_per_trade": 0.05,
        "max_position_pct": 0.80,
        "max_total_exposure_pct": 0.98,
        "max_daily_loss_pct": 0.15,
        "max_consecutive_losses": 3,
        "max_open_positions": 3,
        "unpriced_asset_dust_quantity": 0.00000001,
    }
    assert rules["trade"]["require_stop_loss"] is True
    assert rules["trade"]["require_take_profit"] is True
    assert rules["execution"]["demo_only"] is True
    assert rules["execution"]["require_verified_protection"] is True
    assert rules["execution"]["safe_retry_after_reconciliation"] is False
    assert config.environments["environments"]["live"]["enabled"] is False
