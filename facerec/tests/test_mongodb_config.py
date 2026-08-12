from app.core.config import DBSettings, apply_db_environment
from pymongo.uri_parser import parse_uri


def test_mongodb_environment_overrides_deployment_credentials(monkeypatch) -> None:
    monkeypatch.setenv("FACEREC_MONGO_USERNAME", "face@operator")
    monkeypatch.setenv("FACEREC_MONGO_PASSWORD", "p:a/ss? word+")
    config = {
        "username": "root",
        "password": "root",
        "host": "mongodb",
        "port": "27017",
        "database": "facerecapi",
        "auth_source": "admin",
    }

    settings = DBSettings(**apply_db_environment(config))

    assert settings.username == "face@operator"
    assert settings.password == "p:a/ss? word+"
    assert settings.url == (
        "mongodb://face%40operator:p%3Aa%2Fss%3F%20word%2B@"
        "mongodb:27017/facerecapi?authSource=admin"
    )
    parsed = parse_uri(settings.url)
    assert parsed["username"] == settings.username
    assert parsed["password"] == settings.password
