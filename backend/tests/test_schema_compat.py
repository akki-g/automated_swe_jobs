from sqlalchemy import create_engine, inspect

from app.db.session import _ensure_web_profile_columns


def test_web_profile_schema_upgrade_is_additive_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                email VARCHAR(255),
                sms_provider VARCHAR(20) NOT NULL,
                opted_out BOOLEAN NOT NULL,
                consent_at DATETIME,
                consent_method VARCHAR(50) NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE criteria (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                role_types JSON NOT NULL,
                keywords JSON NOT NULL,
                locations JSON NOT NULL,
                sponsorship_required BOOLEAN,
                min_date DATETIME,
                freeform_notes VARCHAR(2000) NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

        _ensure_web_profile_columns(connection)
        _ensure_web_profile_columns(connection)

        schema = inspect(connection)
        user_columns = {column["name"] for column in schema.get_columns("users")}
        criteria_columns = {column["name"] for column in schema.get_columns("criteria")}
        assert {"password_hash", "profile_completed_at", "email_digest_enabled"} <= user_columns
        assert {"target_fields", "resume_profile", "resume_updated_at"} <= criteria_columns
