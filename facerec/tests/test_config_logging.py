import logging

from app.core import config


def test_load_config_does_not_log_database_password(caplog, tmp_path, monkeypatch) -> None:
    password = "never-log-this-password"
    config_file = tmp_path / "config.toml"
    source = config.config_path.read_text(encoding="utf-8")
    source = source.replace('password = "root"', f'password = "{password}"', 1)
    config_file.write_text(source, encoding="utf-8")
    monkeypatch.setattr(config, "config_path", config_file)
    monkeypatch.delenv("FACEREC_MONGO_PASSWORD", raising=False)

    with caplog.at_level(logging.DEBUG, logger=config.__name__):
        config.load_config()

    assert password not in caplog.text
