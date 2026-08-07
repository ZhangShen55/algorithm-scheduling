from services.orchestrator_service.app.application.factory import create_app

app = create_app()

__all__ = ["app", "create_app"]
